-- Retention systems: 7-day login streak, session playtime gifts, 3 daily
-- quests, codes, group bonus and onboarding steps. Every reward is free; cash
-- rewards scale with the player's income so they stay meaningful.
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Config = require(Shared:WaitForChild("Config"))
local Economy = require(Shared:WaitForChild("Economy"))
local Format = require(Shared:WaitForChild("Format"))
local Rarities = require(Shared:WaitForChild("Rarities"))
local RewardDefs = require(Shared:WaitForChild("RewardDefs"))
local Slimes = require(Shared:WaitForChild("Slimes"))

local RewardService = {}

local S
local rng = Random.new()
local sessions: { [Player]: { start: number, claimed: { [number]: boolean } } } = {}

RewardService.Daily = RewardDefs.Daily
RewardService.Gifts = RewardDefs.Gifts

local QUEST_POOL = {
	{ kind = "buy", target = 12, text = "Buy %s slimes", reward = { cashMinutes = 10, minCash = 2000 } },
	{ kind = "collect", target = 0, text = "Collect %s cash", reward = { boostMinutes = 10 } },
	{ kind = "steal", target = 2, text = "Steal %s slimes", reward = { cashMinutes = 20, minCash = 5000 } },
	{ kind = "fuse", target = 1, text = "Fuse %s slime", reward = { cashMinutes = 15, minCash = 4000 } },
	{ kind = "levelup", target = 8, text = "Level up slimes %s times", reward = { cashMinutes = 12, minCash = 3000 } },
	{ kind = "bonk", target = 3, text = "Bonk %s thieves", reward = { boostMinutes = 15 } },
	{ kind = "playtime", target = 20, text = "Play for %s minutes", reward = { slimeMinRarity = "Rare" } },
}

local function today(): number
	return os.time() // 86400
end

local function profileOf(player: Player)
	return S.DataService.get(player)
end

-- reward: { cashMinutes?, minCash?, cash?, boostMinutes?, slimeMinRarity? }
-- Returns a short description of what was granted.
function RewardService.grant(player: Player, reward, sku: string): string
	local parts = {}
	if reward.cashMinutes or reward.minCash or reward.cash then
		local amount = reward.cash
			or Economy.scaledCash(S.EconomyService.baseIncomePerSecond(player), reward.cashMinutes or 0, reward.minCash or 0)
		S.EconomyService.addCash(player, amount, sku, "TimedReward")
		table.insert(parts, Format.cash(amount))
	end
	if reward.boostMinutes then
		S.EconomyService.addBoost(player, "cash2x", reward.boostMinutes * 60)
		table.insert(parts, "2x Cash " .. reward.boostMinutes .. "m")
	end
	if reward.slimeMinRarity then
		local minRank = Rarities.ByKey[reward.slimeMinRarity].rank
		local id, mut = Economy.rollSpawn(rng, 1, 1, minRank)
		local slime = S.SlimeService.grantSlime(player, id, mut, { allowOverflow = true })
		if slime then
			table.insert(parts, Slimes.ById[id].name .. " (" .. Slimes.ById[id].rarity .. ")")
		else
			local amount = Economy.slimePrice(id, mut)
			S.EconomyService.addCash(player, amount, sku .. "_SlimeCash", "TimedReward")
			table.insert(parts, Format.cash(amount))
		end
	end
	local text = table.concat(parts, " + ")
	S.PlayerService.fx(player, "celebrate", { text = text })
	return text
end

function RewardService.tutorialStep(player: Player, step: number)
	local profile = profileOf(player)
	if not profile or profile.data.tutorial >= step then
		return
	end
	profile.data.tutorial = step
	S.Analytics.onboarding(player, step + 1, "Step" .. step)
	S.PlayerService.sync(player, { tutorial = step })
end

---------------------------------------------------------------------------
-- Daily streak
---------------------------------------------------------------------------
local function dailyState(profile)
	local daily = profile.data.daily
	local t = today()
	local streakAlive = daily.lastDay >= t - 1
	local nextStreak = if daily.lastDay == t then daily.streak elseif streakAlive then daily.streak + 1 else 1
	return {
		streak = daily.streak,
		canClaim = daily.lastDay < t,
		nextDay = (nextStreak - 1) % #RewardService.Daily + 1,
		resetIn = (t + 1) * 86400 - os.time(),
	}
end

local function claimDaily(player: Player)
	local profile = profileOf(player)
	local daily = profile.data.daily
	local t = today()
	if daily.lastDay >= t then
		return { ok = false, msg = "Come back tomorrow!" }
	end
	daily.streak = if daily.lastDay == t - 1 then daily.streak + 1 else 1
	daily.lastDay = t
	local day = (daily.streak - 1) % #RewardService.Daily + 1
	local text = RewardService.grant(player, RewardService.Daily[day], "Daily" .. day)
	S.PlayerService.notify(player, "📅 Day " .. day .. " reward: " .. text, "success")
	S.PlayerService.sync(player, { daily = dailyState(profile) })
	return { ok = true }
end

---------------------------------------------------------------------------
-- Session gifts
---------------------------------------------------------------------------
local function giftState(player: Player)
	local session = sessions[player]
	if not session then
		return nil
	end
	local claimed = {}
	for i in session.claimed do
		table.insert(claimed, i)
	end
	return {
		startedAt = workspace:GetServerTimeNow() - (os.clock() - session.start),
		claimed = claimed,
	}
end

local function claimGift(player: Player, index: any)
	local session = sessions[player]
	local gift = RewardService.Gifts[tonumber(index) or 0]
	if not session or not gift then
		return { ok = false }
	end
	if session.claimed[index] then
		return { ok = false, msg = "Already claimed!" }
	end
	if (os.clock() - session.start) < gift.at * 60 then
		return { ok = false, msg = "Not ready yet!" }
	end
	session.claimed[index] = true
	local text = RewardService.grant(player, gift.reward, "Gift" .. index)
	S.PlayerService.notify(player, "🎁 Gift: " .. text, "success")
	S.PlayerService.sync(player, { gifts = giftState(player) })
	return { ok = true }
end

---------------------------------------------------------------------------
-- Quests
---------------------------------------------------------------------------
local function questState(profile)
	return profile.data.quests.list
end

local function ensureQuests(player: Player, profile)
	local quests = profile.data.quests
	local t = today()
	if quests.day == t and #quests.list > 0 then
		return
	end
	local seeded = Random.new(player.UserId * 7 + t)
	local pool = table.clone(QUEST_POOL)
	local list = {}
	for _ = 1, 3 do
		local def = table.remove(pool, seeded:NextInteger(1, #pool))
		local target = def.target
		if def.kind == "collect" then
			target = math.max(1500, math.floor(S.EconomyService.baseIncomePerSecond(player) * 60 * 15))
		end
		table.insert(list, {
			kind = def.kind,
			target = target,
			progress = 0,
			text = string.format(def.text, if def.kind == "collect" then Format.cash(target) else tostring(target)),
			reward = def.reward,
			done = false,
			claimed = false,
		})
	end
	quests.day = t
	quests.list = list
end

function RewardService.progress(player: Player, kind: string, amount: number)
	local profile = profileOf(player)
	if not profile then
		return
	end
	local changed = false
	for _, quest in profile.data.quests.list do
		if quest.kind == kind and not quest.done then
			quest.progress = math.min(quest.target, quest.progress + amount)
			changed = true
			if quest.progress >= quest.target then
				quest.done = true
				S.PlayerService.notify(player, "📜 Quest complete: " .. quest.text .. "! Claim your reward.", "success")
			end
		end
	end
	if changed then
		S.PlayerService.sync(player, { quests = questState(profile) })
	end
end

local function claimQuest(player: Player, index: any)
	local profile = profileOf(player)
	local quest = profile.data.quests.list[tonumber(index) or 0]
	if not quest or not quest.done or quest.claimed then
		return { ok = false }
	end
	quest.claimed = true
	local text = RewardService.grant(player, quest.reward, "Quest_" .. quest.kind)
	S.PlayerService.notify(player, "📜 Quest reward: " .. text, "success")
	S.PlayerService.sync(player, { quests = questState(profile) })
	return { ok = true }
end

---------------------------------------------------------------------------
-- Codes & group
---------------------------------------------------------------------------
local function redeem(player: Player, raw: any)
	if type(raw) ~= "string" or #raw > 40 then
		return { ok = false, msg = "Invalid code" }
	end
	local code = string.upper((string.gsub(raw, "%s", "")))
	local def = Config.Codes[code]
	local profile = profileOf(player)
	if not def then
		return { ok = false, msg = "Invalid code" }
	end
	if profile.data.codes[code] then
		return { ok = false, msg = "Already redeemed!" }
	end
	if def.expires and os.time() > def.expires then
		return { ok = false, msg = "This code has expired" }
	end
	profile.data.codes[code] = true
	local text = RewardService.grant(player, def, "Code_" .. code)
	S.PlayerService.notify(player, "🔑 Code redeemed: " .. text, "success")
	return { ok = true, msg = text }
end

local function checkGroup(player: Player): boolean
	if Config.GroupId <= 0 then
		return false
	end
	local ok, inGroup = pcall(function()
		return player:IsInGroupAsync(Config.GroupId)
	end)
	local member = ok and inGroup == true
	player:SetAttribute("InGroup", member)
	return member
end

function RewardService.init(services)
	S = services
	S.PlayerService.onReady(function(player, profile)
		sessions[player] = { start = os.clock(), claimed = {} }
		ensureQuests(player, profile)
		checkGroup(player)
	end)
	S.PlayerService.onLeaving(function(player)
		sessions[player] = nil
	end)
	S.PlayerService.addState(function(player, profile)
		return {
			daily = dailyState(profile),
			gifts = giftState(player),
			quests = questState(profile),
			inGroup = player:GetAttribute("InGroup") == true,
			groupId = Config.GroupId,
			socialText = Config.SocialText,
		}
	end)
	S.PlayerService.registerAction("claimDaily", claimDaily)
	S.PlayerService.registerAction("claimGift", claimGift)
	S.PlayerService.registerAction("claimQuest", claimQuest)
	S.PlayerService.registerAction("redeem", redeem)
	S.PlayerService.registerAction("checkGroup", function(player)
		local was = player:GetAttribute("InGroup") == true
		local now = checkGroup(player)
		if now and not was then
			S.PlayerService.notify(player, "👥 Thanks for joining! +" .. math.floor(Config.GroupBonus * 100) .. "% cash forever!",
				"success")
		end
		return { ok = true, inGroup = now }
	end)
end

function RewardService.start()
	while true do
		task.wait(60)
		for _, player in S.PlayerService.readyPlayers() do
			local profile = profileOf(player)
			if profile then
				ensureQuests(player, profile)
				RewardService.progress(player, "playtime", 1)
			end
		end
	end
end

return RewardService
