-- Cash, multipliers, per-second income, slime growth, boosts, offline earnings
-- and rebirths. All cash changes go through addCash/spend.
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Config = require(Shared:WaitForChild("Config"))
local Economy = require(Shared:WaitForChild("Economy"))
local Format = require(Shared:WaitForChild("Format"))

local Tuning = Config.Tuning
local EconomyService = {}

local S
local stored: { [Player]: { [number]: number } } = {}
local incomeCache: { [Player]: number } = {}
local earnedAgg: { [Player]: number } = {}

local function profileOf(player: Player)
	return S.DataService.get(player)
end

function EconomyService.boostLeft(player: Player, key: string): number
	local profile = profileOf(player)
	return if profile then (profile.data.boosts[key] or 0) else 0
end

function EconomyService.addBoost(player: Player, key: string, seconds: number)
	local profile = profileOf(player)
	if not profile then
		return
	end
	profile.data.boosts[key] = (profile.data.boosts[key] or 0) + seconds
	if key == "shield" then
		S.PlotService.onShieldChanged(player)
	end
	S.PlayerService.sync(player, { boosts = profile.data.boosts })
end

-- Total cash multiplier. includeTemporary=false excludes potions (used for
-- scaled rewards so buying a potion doesn't inflate cash packs).
function EconomyService.multiplier(player: Player, includeTemporary: boolean?): (number, { [string]: number })
	local profile = profileOf(player)
	local parts = {}
	local total = 1
	if not profile then
		return total, parts
	end
	local function add(name: string, mult: number)
		if mult ~= 1 then
			parts[name] = mult
			total *= mult
		end
	end
	local data = profile.data
	add("Rebirths", Economy.rebirthMult(data.rebirths))
	if S.MonetizationService.has(player, "DoubleCash") then
		add("2x Cash Pass", 2)
	end
	if S.MonetizationService.has(player, "VIP") then
		add("VIP", 1.5)
	end
	if includeTemporary ~= false and (data.boosts.cash2x or 0) > 0 then
		add("2x Potion", 2)
	end
	if player:GetAttribute("InGroup") then
		add("Group", 1 + Config.GroupBonus)
	end
	if S.MonetizationService.isPremium(player) then
		add("Premium", 1 + Config.PremiumBonus)
	end
	if S.MonetizationService.isClub(player) then
		add(Config.SubscriptionName, 1 + Config.SubscriptionBonus)
	end
	local friends = S.PlayerService.friendCount(player)
	if friends > 0 then
		add("Friends", 1 + math.min(friends * Tuning.FriendBonusEach, Tuning.FriendBonusMax))
	end
	local discovered = 0
	for _ in data.index do
		discovered += 1
	end
	if discovered > 0 then
		add("Index", 1 + discovered * Tuning.IndexBonusEach)
	end
	return total, parts
end

function EconomyService.incomePerSecond(player: Player): number
	return incomeCache[player] or 0
end

function EconomyService.baseIncomePerSecond(player: Player): number
	local profile = profileOf(player)
	if not profile then
		return 0
	end
	local mult = EconomyService.multiplier(player, false)
	local total = 0
	for _, slime in profile.data.slimes do
		total += Economy.slimeIncome(slime)
	end
	return total * mult
end

local function publishCash(player: Player, profile)
	S.PlayerService.setStat(player, "Cash", Format.cash(profile.data.cash))
end

-- sku/transactionType are logged to Creator Analytics when given.
function EconomyService.addCash(player: Player, amount: number, sku: string?, transactionType: string?)
	local profile = profileOf(player)
	if not profile or amount ~= amount or amount <= 0 then
		return
	end
	amount = math.min(amount, 1e300)
	profile.data.cash += amount
	profile.data.totalEarned += amount
	if sku then
		S.Analytics.economy(player, "Source", amount, profile.data.cash, transactionType or "Gameplay", sku)
	else
		earnedAgg[player] = (earnedAgg[player] or 0) + amount
	end
	publishCash(player, profile)
	S.PlayerService.sync(player, { cash = profile.data.cash, totalEarned = profile.data.totalEarned })
end

function EconomyService.spend(player: Player, amount: number, sku: string): boolean
	local profile = profileOf(player)
	if not profile or amount ~= amount or amount < 0 or profile.data.cash < amount then
		return false
	end
	profile.data.cash -= amount
	S.Analytics.economy(player, "Sink", amount, profile.data.cash, "Shop", sku)
	publishCash(player, profile)
	S.PlayerService.sync(player, { cash = profile.data.cash })
	return true
end

function EconomyService.collectPad(player: Player, padIndex: number)
	local playerStored = stored[player]
	local amount = playerStored and playerStored[padIndex] or 0
	if amount < 1 then
		return
	end
	playerStored[padIndex] = 0
	EconomyService.addCash(player, amount)
	local plot = S.PlotService.get(player)
	if plot then
		S.PlotService.setPadStored(plot, padIndex, 0)
		local pad = plot.pads[padIndex]
		S.PlayerService.fx(player, "cash", { position = pad.collect.Position, amount = amount })
	end
	S.RewardService.progress(player, "collect", amount)
	S.RewardService.tutorialStep(player, 2)
end

function EconomyService.clearPad(player: Player, padIndex: number)
	local playerStored = stored[player]
	if playerStored and (playerStored[padIndex] or 0) > 0 then
		local amount = playerStored[padIndex]
		playerStored[padIndex] = 0
		EconomyService.addCash(player, amount)
	end
	local plot = S.PlotService.get(player)
	if plot then
		S.PlotService.setPadStored(plot, padIndex, 0)
	end
end

local function collectAll(player: Player)
	local playerStored = stored[player]
	if not playerStored then
		return
	end
	local total = 0
	for i, amount in playerStored do
		total += amount
		playerStored[i] = 0
	end
	if total > 0 then
		EconomyService.addCash(player, total)
	end
end

local function offlineCapHours(player: Player): number
	if S.MonetizationService.isClub(player) then
		return Tuning.OfflineCapHoursClub
	elseif S.MonetizationService.has(player, "VIP") then
		return Tuning.OfflineCapHoursVIP
	end
	return Tuning.OfflineCapHours
end

local function computeOffline(player: Player, profile)
	local data = profile.data
	if data.offlinePending > 0 or profile.lastOnline <= 0 or #data.slimes == 0 then
		return
	end
	local away = os.time() - profile.lastOnline
	if away < 60 then
		return
	end
	local seconds = math.min(away, offlineCapHours(player) * 3600)
	local amount = Economy.offlineEarnings(data.slimes, EconomyService.multiplier(player, false), seconds)
	if amount >= 1 then
		data.offlinePending = amount
		profile.offlineSeconds = seconds
	end
end

function EconomyService.claimOffline(player: Player, factor: number, sku: string)
	local profile = profileOf(player)
	if not profile or profile.data.offlinePending <= 0 then
		return 0
	end
	local amount = profile.data.offlinePending * factor
	profile.data.offlinePending = 0
	EconomyService.addCash(player, amount, sku, if factor > 1 then "IAP" else "TimedReward")
	S.PlayerService.sync(player, { offline = false })
	S.PlayerService.fx(player, "celebrate", { text = "+" .. Format.cash(amount) })
	return amount
end

function EconomyService.rebirth(player: Player)
	local profile = profileOf(player)
	local data = profile.data
	local cost = Economy.rebirthCost(data.rebirths)
	if data.cash < cost then
		return { ok = false, msg = "You need " .. Format.cash(cost) .. " to rebirth!" }
	end
	S.Analytics.economy(player, "Sink", cost, data.cash - cost, "Gameplay", "Rebirth")
	data.cash = Tuning.StartingCash
	data.rebirths += 1
	player:SetAttribute("Rebirths", data.rebirths)
	S.PlayerService.setStat(player, "Rebirths", data.rebirths)
	publishCash(player, profile)
	S.PlotService.refreshPads(player)
	S.PlayerService.refreshNametag(player)
	S.Analytics.progression(player, "Rebirth", data.rebirths, "Rebirth " .. data.rebirths)
	S.PlayerService.notifyAll(string.format("⭐ %s reached REBIRTH %d!", player.DisplayName, data.rebirths), "event")
	S.PlayerService.fx(player, "celebrate", { text = "REBIRTH " .. data.rebirths .. "!" })
	S.PlayerService.sync(player, EconomyService.state(player))
	S.PlayerService.sync(player, S.PlotService.state(player))
	return { ok = true }
end

function EconomyService.state(player: Player)
	local profile = profileOf(player)
	local mult, parts = EconomyService.multiplier(player)
	return {
		cash = profile.data.cash,
		totalEarned = profile.data.totalEarned,
		rebirths = profile.data.rebirths,
		income = incomeCache[player] or 0,
		mult = mult,
		multParts = parts,
		boosts = profile.data.boosts,
		offline = if profile.data.offlinePending > 0
			then { amount = profile.data.offlinePending, seconds = profile.offlineSeconds or 0 }
			else false,
	}
end

local function tickPlayer(player: Player, profile)
	local data = profile.data
	local mult = EconomyService.multiplier(player)
	local auto = S.MonetizationService.has(player, "AutoCollect")
	local plot = S.PlotService.get(player)
	local playerStored = stored[player]
	if not playerStored then
		playerStored = {}
		stored[player] = playerStored
	end
	local total = 0
	for _, slime in data.slimes do
		if not slime.carried then
			local income = Economy.slimeIncome(slime) * mult
			total += income
			if not auto then
				playerStored[slime.pad] = (playerStored[slime.pad] or 0) + income
				if plot then
					S.PlotService.setPadStored(plot, slime.pad, playerStored[slime.pad])
				end
			end
			if slime.lvl < Economy.LEVEL_MAX then
				slime.xp = (slime.xp or 0) + 1
				if slime.xp >= Economy.xpToNext(slime.lvl) then
					slime.xp = 0
					slime.lvl += 1
					S.SlimeService.onLevelUp(player, slime)
				end
			end
		end
	end
	incomeCache[player] = total
	if auto and total > 0 then
		EconomyService.addCash(player, total)
	end
	for key, left in data.boosts do
		if left > 0 then
			data.boosts[key] = math.max(0, left - 1)
			if data.boosts[key] == 0 then
				if key == "shield" then
					S.PlotService.onShieldChanged(player)
					S.PlayerService.notify(player, "🛡️ Your base shield expired.", "warning")
				elseif key == "cash2x" then
					S.PlayerService.notify(player, "🧪 2x Cash boost ended.", "info")
				end
			end
		end
	end
	S.PlayerService.sync(player, {
		cash = data.cash,
		income = total,
		baseIncome = EconomyService.baseIncomePerSecond(player),
		mult = mult,
		boosts = data.boosts,
	})
end

function EconomyService.init(services)
	S = services
	S.PlayerService.onReady(function(player, profile)
		stored[player] = {}
		computeOffline(player, profile)
	end)
	S.PlayerService.onLeaving(function(player)
		collectAll(player)
		local agg = earnedAgg[player]
		if agg and agg > 0 then
			local profile = profileOf(player)
			S.Analytics.economy(player, "Source", agg, profile.data.cash, "Gameplay", "SlimeIncome")
		end
		stored[player] = nil
		incomeCache[player] = nil
		earnedAgg[player] = nil
	end)
	S.PlayerService.addState(function(player)
		return EconomyService.state(player)
	end)
	S.PlayerService.registerAction("rebirth", function(player)
		return EconomyService.rebirth(player)
	end)
	S.PlayerService.registerAction("claimOffline", function(player, mode)
		if mode == "ad" then
			return S.MonetizationService.showAd(player, "offline")
		elseif mode == "robux" then
			return S.MonetizationService.promptProduct(player, "OfflineDouble")
		end
		EconomyService.claimOffline(player, 1, "OfflineEarnings")
		return { ok = true }
	end)
end

function EconomyService.start()
	local lastFlush = os.clock()
	while true do
		task.wait(1)
		for _, player in S.PlayerService.readyPlayers() do
			local profile = profileOf(player)
			if profile then
				local ok, err = pcall(tickPlayer, player, profile)
				if not ok then
					warn("[EconomyService] tick failed: " .. tostring(err))
				end
			end
		end
		if os.clock() - lastFlush > 60 then
			lastFlush = os.clock()
			for player, amount in earnedAgg do
				local profile = profileOf(player)
				if profile and amount > 0 then
					S.Analytics.economy(player, "Source", amount, profile.data.cash, "Gameplay", "SlimeIncome")
				end
				earnedAgg[player] = 0
			end
		end
	end
end

return EconomyService
