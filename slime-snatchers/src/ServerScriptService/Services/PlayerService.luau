-- Player lifecycle hub: loads data, runs every service's ready/leave hooks in
-- order, spawns characters at their base, and owns the single validated,
-- rate-limited Action remote plus Sync/Notify/FX helpers.
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Remotes = require(Shared:WaitForChild("Remotes"))
local Config = require(Shared:WaitForChild("Config"))
local Format = require(Shared:WaitForChild("Format"))
local New = require(Shared:WaitForChild("New"))

local PlayerService = {}

local S
local actions = {}
local readyHooks = {}
local leaveHooks = {}
local stateProviders = {}
local ready: { [Player]: boolean } = {}
local buckets: { [Player]: { tokens: number, t: number } } = {}
local friendCounts: { [Player]: number } = {}
local stunnedUntil: { [Player]: number } = {}

local BUCKET_SIZE = 16
local BUCKET_REFILL = 8 -- tokens per second

function PlayerService.registerAction(name: string, handler)
	assert(actions[name] == nil, "duplicate action " .. name)
	actions[name] = handler
end

function PlayerService.onReady(fn)
	table.insert(readyHooks, fn)
end

function PlayerService.onLeaving(fn)
	table.insert(leaveHooks, fn)
end

function PlayerService.addState(fn)
	table.insert(stateProviders, fn)
end

function PlayerService.isReady(player: Player): boolean
	return ready[player] == true
end

function PlayerService.readyPlayers(): { Player }
	local list = {}
	for player in ready do
		if player.Parent then
			table.insert(list, player)
		end
	end
	return list
end

function PlayerService.sync(player: Player, partial)
	if ready[player] and player.Parent then
		Remotes.get("Sync"):FireClient(player, partial)
	end
end

function PlayerService.notify(player: Player, text: string, kind: string?)
	if player.Parent then
		Remotes.get("Notify"):FireClient(player, text, kind or "info")
	end
end

function PlayerService.notifyAll(text: string, kind: string?)
	Remotes.get("Notify"):FireAllClients(text, kind or "info")
end

function PlayerService.fx(player: Player, name: string, data)
	if player.Parent then
		Remotes.get("FX"):FireClient(player, name, data)
	end
end

function PlayerService.fxAll(name: string, data)
	Remotes.get("FX"):FireAllClients(name, data)
end

function PlayerService.fullState(player: Player)
	local profile = S.DataService.get(player)
	if not profile then
		return nil
	end
	local state = { full = true }
	for _, provider in stateProviders do
		local ok, partial = pcall(provider, player, profile)
		if ok and partial then
			for k, v in partial do
				state[k] = v
			end
		elseif not ok then
			warn("[PlayerService] state provider failed: " .. tostring(partial))
		end
	end
	return state
end

function PlayerService.friendCount(player: Player): number
	return friendCounts[player] or 0
end

local function takeToken(player: Player): boolean
	local now = os.clock()
	local bucket = buckets[player]
	if not bucket then
		bucket = { tokens = BUCKET_SIZE, t = now }
		buckets[player] = bucket
	end
	bucket.tokens = math.min(BUCKET_SIZE, bucket.tokens + (now - bucket.t) * BUCKET_REFILL)
	bucket.t = now
	if bucket.tokens < 1 then
		return false
	end
	bucket.tokens -= 1
	return true
end

-- Walk speed = base x Turbo pass x carry slowdown (0 while stunned).
function PlayerService.refreshSpeed(player: Player)
	local character = player.Character
	local humanoid = character and character:FindFirstChildOfClass("Humanoid")
	if not humanoid then
		return
	end
	if (stunnedUntil[player] or 0) > os.clock() then
		humanoid.WalkSpeed = 0
		return
	end
	local speed = Config.Tuning.WalkSpeed
	if S.MonetizationService.has(player, "Speed") then
		speed *= Config.Tuning.SpeedPassMult
	end
	if S.CarryService.isCarrying(player) and not S.MonetizationService.has(player, "FastSteal") then
		speed *= Config.Tuning.CarrySpeedMult
	end
	humanoid.WalkSpeed = speed
end

function PlayerService.stun(player: Player, seconds: number)
	stunnedUntil[player] = os.clock() + seconds
	PlayerService.refreshSpeed(player)
	task.delay(seconds + 0.05, function()
		if player.Parent then
			PlayerService.refreshSpeed(player)
		end
	end)
end

local function spawnCharacter(player: Player)
	if not player.Parent then
		return
	end
	local ok = pcall(function()
		player:LoadCharacterAsync()
	end)
	if not ok and player.Parent then
		player:LoadCharacter()
	end
end

local function nametag(player: Player, character: Model)
	local head = character:WaitForChild("Head", 10)
	if not head or not head:IsA("BasePart") then
		return
	end
	local humanoid = character:FindFirstChildOfClass("Humanoid")
	if humanoid then
		humanoid.DisplayDistanceType = Enum.HumanoidDisplayDistanceType.None
	end
	local old = head:FindFirstChild("Nametag")
	if old then
		old:Destroy()
	end
	local gui = New("BillboardGui", {
		Name = "Nametag",
		Size = UDim2.fromScale(6, 1.6),
		StudsOffset = Vector3.new(0, 2.6, 0),
		MaxDistance = 90,
		LightInfluence = 0,
		Adornee = head,
	})
	New("UIListLayout", { SortOrder = Enum.SortOrder.LayoutOrder, Parent = gui })
	local vip = player:GetAttribute("VIP") == true
	New("TextLabel", {
		Name = "Title",
		BackgroundTransparency = 1,
		Size = UDim2.fromScale(1, 0.4),
		Font = Enum.Font.FredokaOne,
		TextScaled = true,
		TextStrokeTransparency = 0.2,
		Text = PlayerService.titleFor(player),
		TextColor3 = if vip then Color3.fromRGB(255, 215, 70) else Color3.fromRGB(170, 220, 255),
		LayoutOrder = 1,
		Parent = gui,
	})
	New("TextLabel", {
		Name = "PlayerName",
		BackgroundTransparency = 1,
		Size = UDim2.fromScale(1, 0.6),
		Font = Enum.Font.FredokaOne,
		TextScaled = true,
		TextStrokeTransparency = 0.1,
		Text = player.DisplayName,
		TextColor3 = if vip then Color3.fromRGB(255, 225, 120) else Color3.fromRGB(255, 255, 255),
		LayoutOrder = 2,
		Parent = gui,
	})
	gui.Parent = head
end

function PlayerService.titleFor(player: Player): string
	local parts = {}
	if player:GetAttribute("Admin") then
		table.insert(parts, "🛠️ DEV")
	end
	if player:GetAttribute("VIP") then
		table.insert(parts, "👑 VIP")
	end
	if player:GetAttribute("Club") then
		table.insert(parts, "💎 " .. Config.SubscriptionName)
	end
	local rebirths = player:GetAttribute("Rebirths") or 0
	if rebirths > 0 then
		table.insert(parts, "⭐ " .. rebirths)
	end
	return table.concat(parts, "  ")
end

function PlayerService.refreshNametag(player: Player)
	local character = player.Character
	if character then
		task.spawn(nametag, player, character)
	end
end

local function onCharacterAdded(player: Player, character: Model)
	local root = character:WaitForChild("HumanoidRootPart", 10)
	local humanoid = character:WaitForChild("Humanoid", 10)
	if not root or not humanoid or not player.Parent then
		return
	end
	local plot = S.PlotService.get(player)
	if plot then
		task.defer(function()
			character:PivotTo(plot.spawn)
		end)
	end
	PlayerService.refreshSpeed(player)
	nametag(player, character)
	S.ToolService.equipStarterTools(player)
	humanoid.Died:Connect(function()
		S.CarryService.cancelFor(player, "fainted")
		task.delay(3, function()
			if player.Parent and player.Character == character then
				spawnCharacter(player)
			end
		end)
	end)
end

local function updateFriends(player: Player)
	for _, other in Players:GetPlayers() do
		if other ~= player then
			task.spawn(function()
				local ok, isFriend = pcall(function()
					return player:IsFriendsWithAsync(other.UserId)
				end)
				if ok and isFriend and player.Parent and other.Parent then
					friendCounts[player] = (friendCounts[player] or 0) + 1
					friendCounts[other] = (friendCounts[other] or 0) + 1
					PlayerService.notify(player, "🤝 Friend bonus! " .. other.DisplayName .. " is here (+5% cash)", "success")
					PlayerService.notify(other, "🤝 Friend bonus! " .. player.DisplayName .. " joined (+5% cash)", "success")
				end
			end)
		end
	end
end

local function setupLeaderstats(player: Player, profile)
	local stats = New("Folder", { Name = "leaderstats" })
	New("StringValue", { Name = "Cash", Value = Format.cash(profile.data.cash), Parent = stats })
	New("IntValue", { Name = "Rebirths", Value = profile.data.rebirths, Parent = stats })
	New("IntValue", { Name = "Steals", Value = profile.data.stats.steals, Parent = stats })
	stats.Parent = player
	player:SetAttribute("Rebirths", profile.data.rebirths)
end

function PlayerService.setStat(player: Player, name: string, value: any)
	local stats = player:FindFirstChild("leaderstats")
	local obj = stats and stats:FindFirstChild(name)
	if obj and (obj:IsA("StringValue") or obj:IsA("IntValue")) then
		(obj :: any).Value = value
	end
end

local function onPlayerAdded(player: Player)
	local profile = S.DataService.load(player)
	if not profile then
		if player.Parent then
			player:Kick("We couldn't load your data. Please rejoin in a minute!")
		end
		return
	end
	if not player.Parent then
		S.DataService.release(player)
		return
	end
	setupLeaderstats(player, profile)
	for _, hook in readyHooks do
		local ok, err = pcall(hook, player, profile)
		if not ok then
			warn("[PlayerService] ready hook failed: " .. tostring(err))
		end
	end
	ready[player] = true
	player.CharacterAdded:Connect(function(character)
		onCharacterAdded(player, character)
	end)
	spawnCharacter(player)
	Remotes.get("Sync"):FireClient(player, PlayerService.fullState(player))
	updateFriends(player)
	S.Analytics.onboarding(player, 1, "Joined")
end

local function onPlayerRemoving(player: Player)
	if ready[player] then
		ready[player] = nil
		for _, hook in leaveHooks do
			local ok, err = pcall(hook, player)
			if not ok then
				warn("[PlayerService] leave hook failed: " .. tostring(err))
			end
		end
		S.DataService.release(player)
	end
	buckets[player] = nil
	stunnedUntil[player] = nil
	friendCounts[player] = nil
	for _, other in Players:GetPlayers() do
		if other ~= player and friendCounts[other] then
			task.spawn(function()
				local ok, isFriend = pcall(function()
					return other:IsFriendsWithAsync(player.UserId)
				end)
				if ok and isFriend and friendCounts[other] then
					friendCounts[other] = math.max(0, friendCounts[other] - 1)
				end
			end)
		end
	end
end

function PlayerService.init(services)
	S = services
	-- characters spawn only after the player's base is assigned
	Players.CharacterAutoLoads = false
	Remotes.init()
	Remotes.get("Action").OnServerInvoke = function(player: Player, name: any, payload: any)
		if type(name) ~= "string" then
			return { ok = false }
		end
		local handler = actions[name]
		if not handler then
			return { ok = false, msg = "Unknown action" }
		end
		if not takeToken(player) then
			return { ok = false, msg = "Slow down!" }
		end
		if name ~= "ready" and not ready[player] then
			return { ok = false, msg = "Loading..." }
		end
		local ok, result = pcall(handler, player, payload)
		if not ok then
			warn(string.format("[Action %s] %s", name, tostring(result)))
			return { ok = false, msg = "Something went wrong" }
		end
		return result or { ok = true }
	end

	PlayerService.registerAction("ready", function(player)
		if not ready[player] then
			return { ok = false, notReady = true }
		end
		return { ok = true, state = PlayerService.fullState(player) }
	end)

	PlayerService.registerAction("settings", function(player, payload)
		local profile = S.DataService.get(player)
		if type(payload) ~= "table" then
			return { ok = false }
		end
		for _, key in { "music", "sfx", "lowGfx" } do
			if type(payload[key]) == "boolean" then
				profile.data.settings[key] = payload[key]
			end
		end
		return { ok = true }
	end)

	PlayerService.registerAction("tutorial", function(player, step)
		local profile = S.DataService.get(player)
		if type(step) == "number" and step > profile.data.tutorial and step <= 10 then
			profile.data.tutorial = math.floor(step)
			S.Analytics.onboarding(player, 1 + profile.data.tutorial, "Tutorial" .. profile.data.tutorial)
		end
		return { ok = true }
	end)

	PlayerService.addState(function(player, profile)
		return {
			settings = profile.data.settings,
			tutorial = profile.data.tutorial,
			stats = profile.data.stats,
			userId = player.UserId,
			memoryMode = S.DataService.isMemoryMode(),
		}
	end)
end

function PlayerService.start()
	Players.PlayerAdded:Connect(onPlayerAdded)
	Players.PlayerRemoving:Connect(onPlayerRemoving)
	-- On shutdown run the normal leave flow (collect pad cash, cancel steals,
	-- save) for everyone, then release any profile that never became ready.
	game:BindToClose(function()
		local pending = 0
		for player in ready do
			pending += 1
			task.spawn(function()
				onPlayerRemoving(player)
				pending -= 1
			end)
		end
		local deadline = os.clock() + 20
		while pending > 0 and os.clock() < deadline do
			task.wait(0.2)
		end
		S.DataService.releaseAll(math.max(1, deadline + 5 - os.clock()))
	end)
	for _, player in Players:GetPlayers() do
		task.spawn(onPlayerAdded, player)
	end
	-- playtime stat
	while true do
		task.wait(60)
		for player in ready do
			local profile = S.DataService.get(player)
			if profile then
				profile.data.stats.playtime += 60
			end
		end
	end
end

return PlayerService
