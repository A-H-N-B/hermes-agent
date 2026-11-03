-- Conveyor spawning, buying (cash or Robux instant-buy of the visible slime),
-- base pads, selling, fusion and growth visuals.
local MarketplaceService = game:GetService("MarketplaceService")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local TweenService = game:GetService("TweenService")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Config = require(Shared:WaitForChild("Config"))
local Economy = require(Shared:WaitForChild("Economy"))
local Format = require(Shared:WaitForChild("Format"))
local Mutations = require(Shared:WaitForChild("Mutations"))
local New = require(Shared:WaitForChild("New"))
local Rarities = require(Shared:WaitForChild("Rarities"))
local SlimeModel = require(Shared:WaitForChild("SlimeModel"))
local Slimes = require(Shared:WaitForChild("Slimes"))

local Tuning = Config.Tuning
local SlimeService = {}

local S
local rng = Random.new()
local belt = {} -- [beltUid] = entry
local beltCount = 0
local beltCounter = 0
local models: { [Player]: { [string]: Model } } = {}
local pendingInstant: { [Player]: any } = {}

local GUARANTEE_SECONDS = 150 -- an Epic-or-better slime spawns at least this often
local INSTANT_RESERVE_SECONDS = 25
SlimeService.GuaranteeSeconds = GUARANTEE_SECONDS

local function profileOf(player: Player)
	return S.DataService.get(player)
end

function SlimeService.find(player: Player, uid: string)
	local profile = profileOf(player)
	if not profile then
		return nil
	end
	for i, slime in profile.data.slimes do
		if slime.uid == uid then
			return slime, i
		end
	end
	return nil
end

function SlimeService.getModel(player: Player, uid: string): Model?
	local list = models[player]
	return list and list[uid]
end

function SlimeService.syncSlimes(player: Player)
	local profile = profileOf(player)
	if not profile then
		return
	end
	S.PlayerService.sync(player, { slimes = profile.data.slimes, index = profile.data.index })
end

-- First free pad index. allowOverflow lets paid grants use pads beyond the
-- unlocked count so a purchase is never lost.
function SlimeService.freePad(player: Player, allowOverflow: boolean?): number?
	local profile = profileOf(player)
	if not profile then
		return nil
	end
	local used = {}
	for _, slime in profile.data.slimes do
		used[slime.pad] = true
	end
	local limit = if allowOverflow then Economy.maxPossiblePads() else S.PlotService.maxPads(player)
	for i = 1, limit do
		if not used[i] then
			return i
		end
	end
	return nil
end

local function padCFrame(plot, padIndex: number, model: Model): CFrame
	local pad = plot.pads[padIndex]
	local position = pad.top.Position + Vector3.new(0, SlimeModel.restHeight(model), 0)
	local outward = -plot.cf.LookVector
	return CFrame.lookAt(position, position + outward)
end

local function addPrompt(parent: Instance, props, mode: string, ownerId: number)
	props.RequiresLineOfSight = false
	props.MaxActivationDistance = props.MaxActivationDistance or 10
	local prompt = New("ProximityPrompt", props)
	prompt:SetAttribute("Mode", mode)
	prompt:SetAttribute("OwnerId", ownerId)
	prompt.Parent = parent
	return prompt
end

local function placeModel(player: Player, slime)
	local plot = S.PlotService.get(player)
	if not plot or not plot.pads[slime.pad] then
		return
	end
	local list = models[player]
	if not list then
		list = {}
		models[player] = list
	end
	if list[slime.uid] then
		list[slime.uid]:Destroy()
	end
	local def = Slimes.ById[slime.id]
	local model = SlimeModel.build(slime.id, slime.mut, slime.lvl, "pad")
	model:SetAttribute("Uid", slime.uid)
	model:SetAttribute("OwnerId", player.UserId)
	model:SetAttribute("Shield", slime.shield or 0)
	local cf = padCFrame(plot, slime.pad, model)
	model:PivotTo(cf)
	model:SetAttribute("BaseCFrame", cf)
	local body = model.PrimaryPart :: BasePart
	local steal = addPrompt(body, {
		Name = "Steal",
		ActionText = "Steal",
		ObjectText = def.name,
		HoldDuration = Tuning.StealHoldSeconds,
		KeyboardKeyCode = Enum.KeyCode.E,
	}, "steal", player.UserId)
	steal.Triggered:Connect(function(thief)
		if thief ~= player then
			S.CarryService.tryStealing(thief, player, slime.uid)
		end
	end)
	local sell = addPrompt(body, {
		Name = "Sell",
		ActionText = "Sell " .. Format.cash(Economy.sellValue(slime)),
		ObjectText = def.name,
		HoldDuration = 1.2,
		KeyboardKeyCode = Enum.KeyCode.R,
	}, "sell", player.UserId)
	sell.Triggered:Connect(function(who)
		if who == player then
			SlimeService.sell(player, slime.uid)
		end
	end)
	model.Parent = workspace:FindFirstChild("BaseSlimes")
	list[slime.uid] = model
	S.PlotService.setPadOccupied(plot, slime.pad, true)
end

function SlimeService.removeModel(player: Player, uid: string)
	local list = models[player]
	local model = list and list[uid]
	if model then
		model:Destroy()
		list[uid] = nil
	end
end

local function discover(player: Player, profile, id: string, mut: string)
	local entry = profile.data.index[id]
	local firstOfType = entry == nil
	if not entry then
		entry = {}
		profile.data.index[id] = entry
	end
	if not entry[mut] then
		entry[mut] = true
		local def = Slimes.ById[id]
		local label = (if mut ~= "None" then mut .. " " else "") .. def.name
		S.PlayerService.notify(player, "📖 New discovery: " .. label .. (if firstOfType then " (+1% cash forever!)" else ""),
			"success")
	end
end

-- opts: { pad = number?, level = number?, shieldSeconds = number?, allowOverflow = boolean? }
function SlimeService.grantSlime(player: Player, id: string, mut: string, opts)
	opts = opts or {}
	local profile = profileOf(player)
	if not profile or not Slimes.ById[id] then
		return nil
	end
	local pad = opts.pad or SlimeService.freePad(player, opts.allowOverflow)
	if not pad then
		return nil
	end
	local slime = {
		uid = S.DataService.nextUid(profile),
		id = id,
		mut = if Mutations.ByKey[mut] then mut else "None",
		lvl = math.clamp(opts.level or 1, 1, Economy.LEVEL_MAX),
		xp = 0,
		pad = pad,
		shield = if opts.shieldSeconds then os.time() + opts.shieldSeconds else nil,
	}
	table.insert(profile.data.slimes, slime)
	discover(player, profile, slime.id, slime.mut)
	placeModel(player, slime)
	SlimeService.syncSlimes(player)
	return slime
end

function SlimeService.removeSlime(player: Player, uid: string)
	local profile = profileOf(player)
	local slime, i = SlimeService.find(player, uid)
	if not slime then
		return nil
	end
	table.remove(profile.data.slimes, i)
	SlimeService.removeModel(player, uid)
	S.EconomyService.clearPad(player, slime.pad)
	local plot = S.PlotService.get(player)
	if plot then
		S.PlotService.setPadOccupied(plot, slime.pad, false)
	end
	SlimeService.syncSlimes(player)
	return slime
end

-- Re-creates the model of a slime that was carried and dropped.
function SlimeService.restoreModel(player: Player, slime)
	slime.carried = nil
	placeModel(player, slime)
end

function SlimeService.onLevelUp(player: Player, slime)
	local model = SlimeService.getModel(player, slime.uid)
	local plot = S.PlotService.get(player)
	if model and plot then
		SlimeModel.setLevel(model, slime.lvl)
		local cf = padCFrame(plot, slime.pad, model)
		model:PivotTo(cf)
		model:SetAttribute("BaseCFrame", cf)
		local sell = (model.PrimaryPart :: BasePart):FindFirstChild("Sell")
		if sell and sell:IsA("ProximityPrompt") then
			sell.ActionText = "Sell " .. Format.cash(Economy.sellValue(slime))
		end
		S.PlayerService.fx(player, "levelup", { position = (model.PrimaryPart :: BasePart).Position, level = slime.lvl })
	end
	if slime.lvl >= Economy.LEVEL_MAX then
		S.PlayerService.notify(player, "🌟 " .. Slimes.ById[slime.id].name .. " reached MAX level!", "success")
	end
	S.RewardService.progress(player, "levelup", 1)
	SlimeService.syncSlimes(player)
end

function SlimeService.sell(player: Player, uid: string)
	local slime = SlimeService.find(player, uid)
	if not slime or slime.carried then
		return { ok = false }
	end
	local value = Economy.sellValue(slime)
	SlimeService.removeSlime(player, uid)
	S.EconomyService.addCash(player, value, "Sell_" .. slime.id, "Gameplay")
	local profile = profileOf(player)
	profile.data.stats.sold += 1
	S.PlayerService.notify(player, "Sold " .. Slimes.ById[slime.id].name .. " for " .. Format.cash(value), "success")
	S.PlayerService.fx(player, "sound", { name = "Cash" })
	return { ok = true }
end

-- 3 slimes of the same kind -> 1 with the next mutation (keeps the best level).
-- Uses the 3 weakest copies so the player's best one survives untouched.
function SlimeService.fuse(player: Player, slimeId: string)
	local profile = profileOf(player)
	if type(slimeId) ~= "string" or not Slimes.ById[slimeId] then
		return { ok = false }
	end
	local copies = {}
	for _, slime in profile.data.slimes do
		if slime.id == slimeId and not slime.carried then
			table.insert(copies, slime)
		end
	end
	if #copies < 3 then
		return { ok = false, msg = "You need 3 of the same slime to fuse." }
	end
	table.sort(copies, function(a, b)
		local ra, rb = Mutations.ByKey[a.mut].rank, Mutations.ByKey[b.mut].rank
		if ra ~= rb then
			return ra < rb
		end
		return a.lvl < b.lvl
	end)
	local used = { copies[1], copies[2], copies[3] }
	local bestMut, bestLevel = "None", 1
	for _, slime in used do
		if Mutations.ByKey[slime.mut].rank > Mutations.ByKey[bestMut].rank then
			bestMut = slime.mut
		end
		bestLevel = math.max(bestLevel, slime.lvl)
	end
	local result = Mutations.next(bestMut)
	if result == bestMut then
		return { ok = false, msg = "These slimes already have the best mutation!" }
	end
	local pad = used[1].pad
	for _, slime in used do
		SlimeService.removeSlime(player, slime.uid)
	end
	local fused = SlimeService.grantSlime(player, slimeId, result, { pad = pad, level = bestLevel })
	profile.data.stats.fused += 1
	S.RewardService.progress(player, "fuse", 1)
	local def = Slimes.ById[slimeId]
	S.PlayerService.notify(player, "✨ Fused into " .. result .. " " .. def.name .. "!", "success")
	local model = fused and SlimeService.getModel(player, fused.uid)
	if model then
		S.PlayerService.fx(player, "fuse", { position = (model.PrimaryPart :: BasePart).Position })
	end
	return { ok = true }
end

local function destroyBeltEntry(entry, poof: boolean)
	belt[entry.uid] = nil
	beltCount -= 1
	if entry.tween then
		entry.tween:Cancel()
	end
	if poof and entry.model.PrimaryPart then
		S.PlayerService.fxAll("poof", { position = entry.model.PrimaryPart.Position })
	end
	entry.model:Destroy()
end

function SlimeService.buyFromBelt(player: Player, uid: string)
	local entry = belt[uid]
	if not entry or not S.PlayerService.isReady(player) then
		return
	end
	if entry.reservedBy and entry.reservedBy ~= player and entry.reservedUntil > os.clock() then
		S.PlayerService.notify(player, "Someone is buying this slime right now!", "warning")
		return
	end
	local pad = SlimeService.freePad(player)
	if not pad then
		S.PlayerService.notify(player, "Your base is full! Sell, fuse or rebirth for more pads.", "error")
		S.PlayerService.fx(player, "baseFull", {})
		return
	end
	local profile = profileOf(player)
	if profile.data.cash < entry.price then
		S.PlayerService.notify(player, "You need " .. Format.cash(entry.price - profile.data.cash) .. " more!", "error")
		S.PlayerService.fx(player, "noCash", { need = entry.price - profile.data.cash })
		return
	end
	if not S.EconomyService.spend(player, entry.price, "Slime_" .. entry.id) then
		return
	end
	destroyBeltEntry(entry, true)
	SlimeService.grantSlime(player, entry.id, entry.mut, { pad = pad })
	profile.data.stats.bought += 1
	S.RewardService.progress(player, "buy", 1)
	S.RewardService.tutorialStep(player, 1)
	S.PlayerService.fx(player, "sound", { name = "Buy" })
	local rarity = Rarities.ByKey[entry.rarity]
	if rarity.rank >= 5 then
		S.PlayerService.notifyAll(string.format("%s bought a %s %s!", player.DisplayName, entry.rarity,
			Slimes.ById[entry.id].name), "event")
	end
end

function SlimeService.requestInstant(player: Player, uid: string)
	local entry = belt[uid]
	if not entry then
		return
	end
	local productKey = "Instant" .. entry.rarity
	if not S.MonetizationService.isConfigured("product", productKey) then
		return
	end
	if not SlimeService.freePad(player, true) then
		S.PlayerService.notify(player, "Your base is completely full!", "error")
		return
	end
	if entry.reservedBy and entry.reservedBy ~= player and entry.reservedUntil > os.clock() then
		S.PlayerService.notify(player, "Someone else is buying this slime!", "warning")
		return
	end
	entry.reservedBy = player
	entry.reservedUntil = os.clock() + INSTANT_RESERVE_SECONDS
	pendingInstant[player] = { id = entry.id, mut = entry.mut, uid = uid, product = productKey }
	S.MonetizationService.promptProduct(player, productKey)
end

-- Receipt handler for Instant* products. Grants the exact slime the buyer saw;
-- if the server lost that context (e.g. it restarted) the fallback is the base
-- slime of that rarity, as stated in the product description.
function SlimeService.grantInstant(player: Player, productKey: string, rarityKey: string)
	local pending = pendingInstant[player]
	local id, mut
	if pending and pending.product == productKey then
		id, mut = pending.id, pending.mut
		pendingInstant[player] = nil
		local entry = belt[pending.uid]
		if entry then
			destroyBeltEntry(entry, true)
		end
	else
		id, mut = Slimes.ByRarity[rarityKey][1].id, "None"
	end
	local slime = SlimeService.grantSlime(player, id, mut, {
		allowOverflow = true,
		shieldSeconds = Tuning.RobuxSlimeShieldMinutes * 60,
	})
	if not slime then
		-- all pads full: never lose a purchase, pay out generously in cash instead
		S.EconomyService.addCash(player, Economy.slimePrice(id, mut) * 2, "InstantOverflow", "IAP")
		S.PlayerService.notify(player, "Base full! You got " .. Format.cash(Economy.slimePrice(id, mut) * 2)
			.. " instead.", "warning")
		return
	end
	S.PlayerService.notifyAll(string.format("💎 %s instantly got a %s %s!", player.DisplayName, rarityKey,
		Slimes.ById[id].name), "purchase")
	S.PlayerService.fx(player, "celebrate", { text = Slimes.ById[id].name .. "!" })
end

function SlimeService.spawnOnBelt(id: string, mut: string)
	local def = Slimes.ById[id]
	local rarity = Rarities.ByKey[def.rarity]
	beltCounter += 1
	local uid = "b" .. beltCounter
	local price = Economy.slimePrice(id, mut)
	local model = SlimeModel.build(id, mut, 1, "belt")
	SlimeModel.makeBillboard(model, { price = price })
	model:SetAttribute("BeltUid", uid)
	local height = SlimeModel.restHeight(model)
	local start = S.WorldBuilder.BeltStart + Vector3.new(0, height, 0)
	local finish = S.WorldBuilder.BeltEnd + Vector3.new(0, height, 0)
	model:PivotTo(CFrame.lookAt(start, finish))
	local body = model.PrimaryPart :: BasePart
	local entry = { uid = uid, id = id, mut = mut, price = price, model = model, rarity = def.rarity }
	local label = (if mut ~= "None" then mut .. " " else "") .. def.name

	local buy = addPrompt(body, {
		Name = "Buy",
		ActionText = "Buy " .. Format.cash(price),
		ObjectText = label .. " (" .. def.rarity .. ")",
		HoldDuration = 0.2,
		KeyboardKeyCode = Enum.KeyCode.E,
		MaxActivationDistance = 12,
	}, "buy", 0)
	buy.Triggered:Connect(function(player)
		SlimeService.buyFromBelt(player, uid)
	end)
	if rarity.rank >= Rarities.ByKey.Epic.rank and S.MonetizationService.isConfigured("product", "Instant" .. def.rarity) then
		local instant = addPrompt(body, {
			Name = "Instant",
			ActionText = "💎 Instant Buy",
			ObjectText = "Robux",
			HoldDuration = 0,
			KeyboardKeyCode = Enum.KeyCode.F,
			GamepadKeyCode = Enum.KeyCode.ButtonY,
			MaxActivationDistance = 12,
			UIOffset = Vector2.new(0, 72),
		}, "instant", 0)
		instant:SetAttribute("Product", "Instant" .. def.rarity)
		instant.Triggered:Connect(function(player)
			SlimeService.requestInstant(player, uid)
		end)
	end

	model.Parent = workspace:FindFirstChild("BeltSlimes")
	belt[uid] = entry
	beltCount += 1
	local tween = TweenService:Create(body, TweenInfo.new(Tuning.BeltTravelTime, Enum.EasingStyle.Linear), {
		CFrame = CFrame.lookAt(finish, finish + (finish - start).Unit),
	})
	entry.tween = tween
	tween.Completed:Connect(function(state)
		if state == Enum.PlaybackState.Completed and belt[uid] == entry then
			destroyBeltEntry(entry, true)
		end
	end)
	tween:Play()

	if rarity.rank >= Rarities.ByKey.Legendary.rank then
		S.PlayerService.notifyAll(string.format("✨ A %s %s appeared on the belt!", def.rarity, label), "rare")
		S.PlayerService.fxAll("rareSpawn", { rarity = def.rarity, name = label })
	end
	return entry
end

function SlimeService.forceSpawn(rarityKey: string)
	local list = Slimes.ByRarity[rarityKey]
	if not list then
		return false
	end
	local def = list[rng:NextInteger(1, #list)]
	local _, mut = Economy.rollSpawn(rng, 1, S.EventService.mutationMult())
	SlimeService.spawnOnBelt(def.id, mut)
	return true
end

local function restore(player: Player, profile)
	-- repair pad indices (config changes or old data) and place every slime
	local used = {}
	local limit = Economy.maxPossiblePads()
	for _, slime in profile.data.slimes do
		slime.carried = nil
		if type(slime.pad) ~= "number" or used[slime.pad] or slime.pad < 1 or slime.pad > limit then
			slime.pad = nil
		else
			used[slime.pad] = true
		end
	end
	for _, slime in profile.data.slimes do
		if not slime.pad then
			for i = 1, limit do
				if not used[i] then
					slime.pad = i
					used[i] = true
					break
				end
			end
		end
	end
	for i = #profile.data.slimes, 1, -1 do
		local slime = profile.data.slimes[i]
		if not slime.pad or not Slimes.ById[slime.id] then
			table.remove(profile.data.slimes, i)
		else
			placeModel(player, slime)
		end
	end
	if #profile.data.slimes == 0 and profile.data.stats.bought == 0 then
		-- every new player starts with one free slime so the loop is obvious
		SlimeService.grantSlime(player, "blubby", "None", {})
	end
end

local function clear(player: Player)
	local list = models[player]
	if list then
		for _, model in list do
			model:Destroy()
		end
	end
	models[player] = nil
	pendingInstant[player] = nil
	for _, entry in belt do
		if entry.reservedBy == player then
			entry.reservedBy = nil
		end
	end
end

function SlimeService.init(services)
	S = services
	S.PlayerService.onReady(restore)
	S.PlayerService.onLeaving(clear)
	S.PlayerService.registerAction("sell", function(player, uid)
		if type(uid) ~= "string" then
			return { ok = false }
		end
		return SlimeService.sell(player, uid)
	end)
	S.PlayerService.registerAction("fuse", function(player, slimeId)
		return SlimeService.fuse(player, slimeId)
	end)
	S.PlayerService.addState(function(_player, profile)
		return { slimes = profile.data.slimes, index = profile.data.index }
	end)
	MarketplaceService.PromptProductPurchaseFinished:Connect(function(userId, _productId, purchased)
		if purchased then
			return
		end
		for player, pending in pendingInstant do
			if player.UserId == userId then
				local entry = belt[pending.uid]
				if entry and entry.reservedBy == player then
					entry.reservedBy = nil
				end
				pendingInstant[player] = nil
			end
		end
	end)
end

function SlimeService.start()
	local lastGuarantee = os.clock()
	while true do
		task.wait(Tuning.SpawnInterval / S.EventService.spawnRateMult())
		if beltCount < 45 and #S.PlayerService.readyPlayers() > 0 then
			local minRank = nil
			if os.clock() - lastGuarantee >= GUARANTEE_SECONDS then
				minRank = Rarities.ByKey.Epic.rank
			end
			local id, mut = Economy.rollSpawn(rng, S.EventService.luck(), S.EventService.mutationMult(), minRank)
			if Rarities.ByKey[Slimes.ById[id].rarity].rank >= Rarities.ByKey.Epic.rank then
				lastGuarantee = os.clock()
			end
			local ok, err = pcall(SlimeService.spawnOnBelt, id, mut)
			if not ok then
				warn("[SlimeService] spawn failed: " .. tostring(err))
			end
		end
	end
end

return SlimeService
