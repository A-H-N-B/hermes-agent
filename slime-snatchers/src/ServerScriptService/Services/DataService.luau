--[[
	Player data with session locking.

	* Load: UpdateAsync claims a lock { job = JobId, t = time }. If another live
	  server holds it (player just hopped servers) we retry, then take it over.
	* Save: refuses to overwrite data whose lock belongs to another server
	  (prevents an old server from rolling back newer progress).
	* Receipts: MonetizationService calls saveNow() before telling Roblox a
	  purchase is granted, so a grant is never lost or applied twice.
	* Studio without "Enable Studio Access to API Services" falls back to memory.
]]
local DataStoreService = game:GetService("DataStoreService")
local RunService = game:GetService("RunService")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Config = require(ReplicatedStorage:WaitForChild("Shared"):WaitForChild("Config"))

local DataService = {}

local JOB_ID = if game.JobId ~= "" then game.JobId else "studio-" .. tostring(math.random(1, 1e9))
local LOCK_RETRIES = 5
local LOCK_RETRY_WAIT = 4
local MAX_RECEIPTS = 120

DataService.Template = {
	version = 1,
	cash = Config.Tuning.StartingCash,
	totalEarned = 0,
	rebirths = 0,
	nextUid = 1,
	slimes = {},
	giftedPasses = {},
	giftCredits = {},
	giftsReceived = {},
	boosts = {},
	index = {},
	daily = { streak = 0, lastDay = 0 },
	quests = { day = 0, list = {} },
	codes = {},
	receipts = {},
	starterPack = false,
	offlinePending = 0,
	stats = {
		steals = 0,
		stolenFrom = 0,
		bought = 0,
		sold = 0,
		fused = 0,
		bonks = 0,
		playtime = 0,
		sessions = 0,
		robuxSpent = 0,
	},
	settings = { music = true, sfx = true, lowGfx = false },
	tutorial = 0,
	firstJoin = 0,
	lastOnline = 0,
}

local profiles: { [Player]: any } = {}
local store: DataStore? = nil
local memoryMode = false
local warnedMemory = false

local function deepCopy(value)
	if type(value) ~= "table" then
		return value
	end
	local out = {}
	for k, v in value do
		out[k] = deepCopy(v)
	end
	return out
end

-- Adds keys that exist in the template but not in saved data (new features).
local function reconcile(data, template)
	for key, value in template do
		if data[key] == nil then
			data[key] = deepCopy(value)
		elseif type(value) == "table" and type(data[key]) == "table" and next(value) ~= nil and #value == 0 then
			reconcile(data[key], value)
		end
	end
end

-- DataStores cannot hold NaN/inf; replace them before every save.
local function sanitize(value)
	if type(value) == "table" then
		for k, v in value do
			if type(v) == "number" and (v ~= v or v == math.huge or v == -math.huge) then
				value[k] = 0
			elseif type(v) == "table" then
				sanitize(v)
			end
		end
	end
end

local function useMemory(reason: string)
	memoryMode = true
	if not warnedMemory then
		warnedMemory = true
		warn("[DataService] Using in-memory data (progress will NOT save): " .. reason
			.. "\nIn Studio: Game Settings > Security > Enable Studio Access to API Services.")
	end
end

local function isStudioAccessError(err: string): boolean
	err = string.lower(tostring(err))
	return RunService:IsStudio() and (string.find(err, "studio", 1, true) ~= nil
		or string.find(err, "403", 1, true) ~= nil
		or string.find(err, "api services", 1, true) ~= nil
		or string.find(err, "publish", 1, true) ~= nil)
end

local function getStore(): DataStore?
	if store or memoryMode then
		return store
	end
	local ok, result = pcall(function()
		return DataStoreService:GetDataStore(Config.DataStoreName)
	end)
	if ok then
		store = result
	else
		useMemory(tostring(result))
	end
	return store
end

local function waitForBudget()
	local deadline = os.clock() + 8
	while os.clock() < deadline do
		local ok, budget = pcall(function()
			return DataStoreService:GetRequestBudgetForRequestType(Enum.DataStoreRequestType.UpdateAsync)
		end)
		if not ok or budget > 0 then
			return
		end
		task.wait(0.5)
	end
end

-- Returns true when UpdateAsync completed (the transform may have cancelled).
local function update(key: string, transform, attempts: number): boolean
	local ds = getStore()
	if not ds then
		return false
	end
	for attempt = 1, attempts do
		waitForBudget()
		local ok, err = pcall(function()
			ds:UpdateAsync(key, transform)
		end)
		if ok then
			return true
		end
		if isStudioAccessError(err) then
			useMemory(tostring(err))
			return false
		end
		warn(string.format("[DataService] UpdateAsync %s failed (%d/%d): %s", key, attempt, attempts, tostring(err)))
		if attempt < attempts then
			task.wait(math.min(2 ^ attempt, 12))
		end
	end
	return false
end

local function newProfile(player: Player, data, memoryOnly: boolean)
	reconcile(data, DataService.Template)
	if data.firstJoin == 0 then
		data.firstJoin = os.time()
	end
	data.stats.sessions += 1
	local profile = {
		player = player,
		key = "u_" .. player.UserId,
		data = data,
		memoryOnly = memoryOnly,
		saving = false,
		lockLost = false,
		sessionStart = os.clock(),
		lastOnline = data.lastOnline,
	}
	profiles[player] = profile
	return profile
end

-- Loads (and locks) a player's data. Returns the profile or nil on failure.
function DataService.load(player: Player)
	local key = "u_" .. player.UserId
	if memoryMode or not getStore() then
		return newProfile(player, deepCopy(DataService.Template), true)
	end

	local loaded = nil
	for attempt = 1, LOCK_RETRIES + 1 do
		local force = attempt > LOCK_RETRIES
		local claimed = nil
		local ok = update(key, function(old)
			claimed = nil
			if type(old) ~= "table" then
				old = deepCopy(DataService.Template)
			end
			local lock = old._lock
			if lock and lock.job ~= JOB_ID and not force then
				return nil -- another server still owns this session
			end
			old._lock = { job = JOB_ID, t = os.time() }
			claimed = old
			return old
		end, 3)
		if memoryMode then
			return newProfile(player, deepCopy(DataService.Template), true)
		end
		if not ok then
			return nil
		end
		if claimed then
			loaded = claimed
			break
		end
		if not player.Parent then
			return nil
		end
		task.wait(LOCK_RETRY_WAIT)
	end
	if not loaded then
		return nil
	end
	if not player.Parent then
		-- left while loading: give the lock back
		update(key, function(old)
			if type(old) == "table" and old._lock and old._lock.job == JOB_ID then
				old._lock = nil
				return old
			end
			return nil
		end, 2)
		return nil
	end
	return newProfile(player, loaded, false)
end

local function doSave(profile, release: boolean): boolean
	while profile.saving do
		task.wait(0.1)
	end
	if profile.memoryOnly then
		return true
	end
	if profile.lockLost then
		return false
	end
	profile.saving = true
	local data = profile.data
	data.lastOnline = os.time()
	sanitize(data)
	local ok = update(profile.key, function(old)
		if type(old) == "table" and old._lock and old._lock.job ~= JOB_ID then
			profile.lockLost = true
			return nil
		end
		data._lock = if release then nil else { job = JOB_ID, t = os.time() }
		return data
	end, if release then 5 else 3)
	profile.saving = false
	if profile.lockLost then
		warn("[DataService] session lock lost for " .. profile.key .. "; not saving")
		local player = profile.player
		if player.Parent and not release then
			player:Kick("Your data was opened in another server. Please rejoin.")
		end
		return false
	end
	return ok
end

function DataService.get(player: Player)
	return profiles[player]
end

function DataService.waitFor(player: Player, timeout: number)
	local deadline = os.clock() + timeout
	while not profiles[player] and player.Parent and os.clock() < deadline do
		task.wait(0.2)
	end
	return profiles[player]
end

function DataService.saveNow(player: Player): boolean
	local profile = profiles[player]
	if not profile then
		return false
	end
	return doSave(profile, false)
end

function DataService.release(player: Player)
	local profile = profiles[player]
	if not profile then
		return
	end
	doSave(profile, true)
	profiles[player] = nil
end

function DataService.hasReceipt(profile, purchaseId: string): boolean
	return table.find(profile.data.receipts, purchaseId) ~= nil
end

function DataService.addReceipt(profile, purchaseId: string)
	local receipts = profile.data.receipts
	table.insert(receipts, purchaseId)
	while #receipts > MAX_RECEIPTS do
		table.remove(receipts, 1)
	end
end

function DataService.nextUid(profile): string
	local n = profile.data.nextUid
	profile.data.nextUid = n + 1
	return "s" .. n
end

-- Admin testing helper: wipe the caller's progress (keeps receipts so purchases stay safe).
function DataService.wipe(player: Player)
	local profile = profiles[player]
	if not profile then
		return
	end
	local receipts = profile.data.receipts
	local lock = profile.data._lock
	local fresh = deepCopy(DataService.Template)
	fresh.receipts = receipts
	fresh._lock = lock
	fresh.firstJoin = os.time()
	profile.data = fresh
end

function DataService.isMemoryMode(): boolean
	return memoryMode
end

function DataService.init(_S)
	getStore()
end

function DataService.start()
	-- autosave, staggered so requests are spread over time
	while true do
		task.wait(Config.Tuning.AutosaveSeconds)
		for player, profile in profiles do
			if player.Parent then
				task.spawn(doSave, profile, false)
				task.wait(1)
			end
		end
	end
end

-- Saves and unlocks every remaining profile (server shutdown).
function DataService.releaseAll(timeout: number)
	local pending = 0
	for player, profile in profiles do
		pending += 1
		task.spawn(function()
			doSave(profile, true)
			profiles[player] = nil
			pending -= 1
		end)
	end
	local deadline = os.clock() + timeout
	while pending > 0 and os.clock() < deadline do
		task.wait(0.2)
	end
end

return DataService
