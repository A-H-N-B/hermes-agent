--[[
	Game passes, developer products, gifting, rewarded video ads, Premium/Plus
	and experience subscriptions.

	Receipt safety (ProcessReceipt):
	  1. wait until the buyer's data is loaded
	  2. skip if this PurchaseId was already granted (stored in player data)
	  3. grant, record the PurchaseId, SAVE, and only then return PurchaseGranted
	A failed save returns NotProcessedYet; Roblox retries and step 2 prevents a
	double grant. Nothing sold here is random: every product grants exactly what
	the shop shows.
]]
local AdService = game:GetService("AdService")
local DataStoreService = game:GetService("DataStoreService")
local MarketplaceService = game:GetService("MarketplaceService")
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Config = require(Shared:WaitForChild("Config"))
local Economy = require(Shared:WaitForChild("Economy"))
local Format = require(Shared:WaitForChild("Format"))

local MonetizationService = {}

local S
local owned: { [Player]: { [string]: boolean } } = {}
local club: { [Player]: boolean } = {}
local prices = { passes = {}, products = {} }
local productKeyById: { [number]: string } = {}
local passKeyById: { [number]: string } = {}
local inFlight: { [string]: boolean } = {}
local pendingGift: { [Player]: { target: number, key: string } } = {}
local pendingAd: { [Player]: string } = {}
local lastAd: { [Player]: number } = {}
local giftStore: DataStore? = nil

local MAX_GIFT_IDS = 200

local function profileOf(player: Player)
	return S.DataService.get(player)
end

function MonetizationService.isConfigured(kind: string, key: string): boolean
	local def = if kind == "pass" then Config.Passes[key] else Config.Products[key]
	return def ~= nil and type(def.id) == "number" and def.id > 0
end

function MonetizationService.has(player: Player, passKey: string): boolean
	local list = owned[player]
	if list and list[passKey] then
		return true
	end
	local profile = profileOf(player)
	return profile ~= nil and profile.data.giftedPasses[passKey] == true
end

function MonetizationService.isPremium(player: Player): boolean
	return player.MembershipType == Enum.MembershipType.Premium
end

function MonetizationService.isClub(player: Player): boolean
	return club[player] == true
end

local function priceOf(kind: string, key: string): number
	local def = if kind == "pass" then Config.Passes[key] else Config.Products[key]
	local cache = if kind == "pass" then prices.passes else prices.products
	return cache[key] or def.price
end

function MonetizationService.state(player: Player)
	local passes = {}
	for key, def in Config.Passes do
		if MonetizationService.isConfigured("pass", key) then
			table.insert(passes, {
				key = key,
				name = def.name,
				icon = def.icon,
				desc = def.desc,
				price = priceOf("pass", key),
				owned = MonetizationService.has(player, key),
				order = def.order,
			})
		end
	end
	local products = {}
	for key, def in Config.Products do
		if MonetizationService.isConfigured("product", key) and not def.hidden then
			table.insert(products, {
				key = key,
				name = def.name,
				icon = def.icon,
				price = priceOf("product", key),
				minutes = def.minutes,
				min = def.min,
				gift = def.gift,
				rarity = def.rarity,
				order = def.order,
			})
		end
	end
	local profile = profileOf(player)
	return {
		passes = passes,
		products = products,
		owned = owned[player] or {},
		gifted = profile.data.giftedPasses,
		giftCredits = profile.data.giftCredits,
		starterPackBought = profile.data.starterPack,
		isPremium = MonetizationService.isPremium(player),
		isClub = MonetizationService.isClub(player),
		clubName = Config.SubscriptionName,
		clubConfigured = Config.SubscriptionId ~= "",
		adsEnabled = Config.RewardedAds.Enabled and MonetizationService.isConfigured("product", "AdReward"),
		adReadyAt = (lastAd[player] or 0) + Config.RewardedAds.CooldownSeconds,
	}
end

local function pushState(player: Player)
	S.PlayerService.sync(player, MonetizationService.state(player))
end

-- Applies side effects of owning a pass (called on join, purchase or gift).
local function applyPass(player: Player, key: string)
	if key == "VIP" then
		player:SetAttribute("VIP", true)
		S.PlayerService.refreshNametag(player)
	elseif key == "ExtraPads" then
		S.PlotService.refreshPads(player)
		S.PlayerService.sync(player, S.PlotService.state(player))
	elseif key == "Speed" or key == "FastSteal" then
		S.PlayerService.refreshSpeed(player)
	end
end

---------------------------------------------------------------------------
-- Gifts: buying a Gift* product adds a credit to the buyer (saved with the
-- receipt). Sending a credit appends to the recipient's queue in a separate
-- DataStore; recipients claim queued gifts when they are online.
---------------------------------------------------------------------------
local function getGiftStore(): DataStore?
	if giftStore or S.DataService.isMemoryMode() then
		return giftStore
	end
	local ok, result = pcall(function()
		return DataStoreService:GetDataStore(Config.GiftStoreName)
	end)
	if ok then
		giftStore = result
	end
	return giftStore
end

local function deliverGiftsTo(player: Player)
	local profile = profileOf(player)
	local ds = getGiftStore()
	if not profile or not ds then
		return
	end
	local key = "g_" .. player.UserId
	local ok, queue = pcall(function()
		return ds:GetAsync(key)
	end)
	if not ok or type(queue) ~= "table" or #queue == 0 then
		return
	end
	local received = profile.data.giftsReceived
	local applied = {}
	for _, gift in queue do
		if type(gift) == "table" and type(gift.id) == "string" then
			table.insert(applied, gift.id)
			if not table.find(received, gift.id) and Config.Passes[gift.key] then
				table.insert(received, gift.id)
				profile.data.giftedPasses[gift.key] = true
				applyPass(player, gift.key)
				S.PlayerService.notify(player, string.format("🎁 %s gifted you %s!", tostring(gift.from),
					Config.Passes[gift.key].name), "purchase")
				S.PlayerService.fx(player, "celebrate", { text = "GIFT: " .. Config.Passes[gift.key].name })
			end
		end
	end
	while #received > MAX_GIFT_IDS do
		table.remove(received, 1)
	end
	if not S.DataService.saveNow(player) then
		return -- keep the queue; dedupe by id makes a retry safe
	end
	pcall(function()
		ds:UpdateAsync(key, function(old)
			if type(old) ~= "table" then
				return nil
			end
			local keep = {}
			for _, gift in old do
				if type(gift) ~= "table" or not table.find(applied, gift.id) then
					table.insert(keep, gift)
				end
			end
			return keep
		end)
	end)
	pushState(player)
end

local function sendGiftCredit(sender: Player, passKey: string, targetUserId: number)
	local profile = profileOf(sender)
	local credits = profile.data.giftCredits
	if (credits[passKey] or 0) < 1 then
		return false, "No gift credit for that item."
	end
	local ds = getGiftStore()
	if not ds then
		return false, "Gifts are unavailable right now."
	end
	local giftId = string.format("%d-%d-%d", sender.UserId, os.time(), math.random(1, 1e6))
	local ok = pcall(function()
		ds:UpdateAsync("g_" .. targetUserId, function(old)
			old = if type(old) == "table" then old else {}
			table.insert(old, { id = giftId, key = passKey, from = sender.DisplayName, fromId = sender.UserId, t = os.time() })
			return old
		end)
	end)
	if not ok then
		return false, "Couldn't send the gift, try again."
	end
	credits[passKey] -= 1
	S.DataService.saveNow(sender)
	local target = Players:GetPlayerByUserId(targetUserId)
	if target and S.PlayerService.isReady(target) then
		task.spawn(deliverGiftsTo, target)
	end
	pushState(sender)
	return true, nil
end

---------------------------------------------------------------------------
-- Product handlers: handler(player, profile, receiptInfo). Must not yield on
-- external services; everything is applied to in-memory data.
---------------------------------------------------------------------------
local handlers = {}

local function cashPack(key: string)
	return function(player: Player)
		local def = Config.Products[key]
		local amount = Economy.scaledCash(S.EconomyService.baseIncomePerSecond(player), def.minutes, def.min)
		S.EconomyService.addCash(player, amount, key, "IAP")
		S.PlayerService.fx(player, "celebrate", { text = "+" .. Format.cash(amount) })
	end
end
for _, key in { "CashSmall", "CashMedium", "CashLarge", "CashMega" } do
	handlers[key] = cashPack(key)
end

function handlers.ServerLuck(player: Player)
	S.EventService.addBoost("luck", Config.Products.ServerLuck.minutes * 60, player.DisplayName)
end

function handlers.ServerRain(player: Player)
	S.EventService.addBoost("rain", Config.Products.ServerRain.minutes * 60, player.DisplayName)
end

function handlers.CashBoost(player: Player)
	S.EconomyService.addBoost(player, "cash2x", Config.Products.CashBoost.minutes * 60)
	S.PlayerService.fx(player, "celebrate", { text = "2x CASH!" })
end

function handlers.Shield(player: Player)
	S.EconomyService.addBoost(player, "shield", Config.Products.Shield.minutes * 60)
	S.PlayerService.notify(player, "🛡️ Base shielded for " .. Config.Products.Shield.minutes .. " minutes!", "success")
end

function handlers.StarterPack(player: Player, profile)
	local income = S.EconomyService.baseIncomePerSecond(player)
	if profile.data.starterPack then
		-- bought twice (e.g. two prompts at once): pay out the cash value instead
		S.EconomyService.addCash(player, Economy.scaledCash(income, 60, 50000), "StarterPackRepeat", "IAP")
		return
	end
	profile.data.starterPack = true
	S.EconomyService.addCash(player, Economy.scaledCash(income, 30, 25000), "StarterPack", "IAP")
	S.EconomyService.addBoost(player, "cash2x", 60 * 60)
	S.SlimeService.grantSlime(player, "noodle", "Golden", {
		allowOverflow = true,
		shieldSeconds = Config.Tuning.RobuxSlimeShieldMinutes * 60,
	})
	S.PlayerService.fx(player, "celebrate", { text = "STARTER PACK!" })
end

function handlers.OfflineDouble(player: Player, profile)
	if profile.data.offlinePending > 0 then
		S.EconomyService.claimOffline(player, 2, "OfflineDouble")
	else
		local amount = Economy.scaledCash(S.EconomyService.baseIncomePerSecond(player), 20, 5000)
		S.EconomyService.addCash(player, amount, "OfflineDoubleFallback", "IAP")
	end
end

for key, def in Config.Products do
	if def.rarity then
		handlers[key] = function(player: Player)
			S.SlimeService.grantInstant(player, key, def.rarity)
		end
	elseif def.gift then
		handlers[key] = function(player: Player, profile)
			local credits = profile.data.giftCredits
			credits[def.gift] = (credits[def.gift] or 0) + 1
		end
	end
end

function handlers.AdReward(player: Player)
	local context = pendingAd[player]
	pendingAd[player] = nil
	if context == "offline" and S.EconomyService.claimOffline(player, 2, "AdOffline") > 0 then
		return
	end
	S.EconomyService.addBoost(player, "cash2x", Config.RewardedAds.BoostMinutes * 60)
	S.PlayerService.notify(player, "📺 Thanks for watching! 2x cash for " .. Config.RewardedAds.BoostMinutes .. " min.",
		"success")
end

local function afterGrant(player: Player, key: string)
	local def = Config.Products[key]
	if def and def.gift then
		local pending = pendingGift[player]
		pendingGift[player] = nil
		if pending and pending.key == key then
			task.spawn(function()
				local ok, err = sendGiftCredit(player, def.gift, pending.target)
				local target = Players:GetPlayerByUserId(pending.target)
				local name = if target then target.DisplayName else "your friend"
				if ok then
					S.PlayerService.notify(player, "🎁 Gift sent to " .. name .. "!", "success")
					S.PlayerService.notifyAll(string.format("🎁 %s gifted %s to %s!", player.DisplayName,
						Config.Passes[def.gift].name, name), "purchase")
				else
					S.PlayerService.notify(player, tostring(err) .. " Your gift is saved in Shop > Gifts.", "warning")
				end
			end)
		end
	end
end

local function processReceipt(info)
	local player = Players:GetPlayerByUserId(info.PlayerId)
	if not player then
		return Enum.ProductPurchaseDecision.NotProcessedYet
	end
	local profile = S.DataService.waitFor(player, 20)
	local deadline = os.clock() + 20
	while profile and not S.PlayerService.isReady(player) and player.Parent and os.clock() < deadline do
		task.wait(0.25)
	end
	if not profile or not S.PlayerService.isReady(player) then
		return Enum.ProductPurchaseDecision.NotProcessedYet
	end
	local purchaseId = tostring(info.PurchaseId)
	if S.DataService.hasReceipt(profile, purchaseId) then
		return Enum.ProductPurchaseDecision.PurchaseGranted
	end
	local key = productKeyById[info.ProductId]
	local handler = key and handlers[key]
	if not handler then
		warn("[Monetization] no handler for product " .. tostring(info.ProductId) .. " (add its id to Config.Products)")
		return Enum.ProductPurchaseDecision.NotProcessedYet
	end
	if inFlight[purchaseId] then
		return Enum.ProductPurchaseDecision.NotProcessedYet
	end
	inFlight[purchaseId] = true
	local ok, err = pcall(handler, player, profile, info)
	if not ok then
		inFlight[purchaseId] = nil
		warn("[Monetization] grant failed for " .. tostring(key) .. ": " .. tostring(err))
		return Enum.ProductPurchaseDecision.NotProcessedYet
	end
	S.DataService.addReceipt(profile, purchaseId)
	local spent = tonumber(info.CurrencySpent) or 0
	profile.data.stats.robuxSpent += spent
	local saved = S.DataService.saveNow(player)
	inFlight[purchaseId] = nil
	if not saved then
		return Enum.ProductPurchaseDecision.NotProcessedYet
	end
	S.Analytics.custom(player, "Purchase_" .. key, spent)
	afterGrant(player, key)
	pushState(player)
	return Enum.ProductPurchaseDecision.PurchaseGranted
end

---------------------------------------------------------------------------
-- Prompts
---------------------------------------------------------------------------
function MonetizationService.promptPass(player: Player, key: string)
	if not MonetizationService.isConfigured("pass", key) then
		return { ok = false, msg = "Not available yet." }
	end
	if MonetizationService.has(player, key) then
		return { ok = false, msg = "You already own this!" }
	end
	S.Analytics.custom(player, "PromptPass_" .. key)
	MarketplaceService:PromptGamePassPurchase(player, Config.Passes[key].id)
	return { ok = true }
end

function MonetizationService.promptProduct(player: Player, key: string)
	if not MonetizationService.isConfigured("product", key) then
		return { ok = false, msg = "Not available yet." }
	end
	local profile = profileOf(player)
	if key == "StarterPack" and profile.data.starterPack then
		return { ok = false, msg = "You already got the Starter Pack!" }
	end
	S.Analytics.custom(player, "PromptProduct_" .. key)
	MarketplaceService:PromptProductPurchase(player, Config.Products[key].id)
	return { ok = true }
end

function MonetizationService.showAd(player: Player, context: string)
	if not (Config.RewardedAds.Enabled and MonetizationService.isConfigured("product", "AdReward")) then
		return { ok = false, msg = "Free rewards are not available." }
	end
	local readyAt = (lastAd[player] or 0) + Config.RewardedAds.CooldownSeconds
	if os.time() < readyAt and context ~= "offline" then
		return { ok = false, msg = "Next free reward in " .. Format.time(readyAt - os.time()) }
	end
	pendingAd[player] = context
	local ok, result = pcall(function()
		local reward = AdService:CreateAdRewardFromDevProductId(Config.Products.AdReward.id)
		return AdService:ShowRewardedVideoAdAsync(player, reward)
	end)
	if ok and result == Enum.ShowAdResult.ShowCompleted then
		lastAd[player] = os.time()
		pushState(player)
		return { ok = true }
	end
	pendingAd[player] = nil
	return { ok = false, msg = "No video available right now — try again later!" }
end

local function checkSubscription(player: Player)
	if Config.SubscriptionId == "" then
		return
	end
	local ok, status = pcall(function()
		return MarketplaceService:GetUserSubscriptionStatusAsync(player, Config.SubscriptionId)
	end)
	local isSub = ok and type(status) == "table" and status.IsSubscribed == true
	club[player] = isSub
	player:SetAttribute("Club", isSub)
end

local function onReady(player: Player)
	owned[player] = {}
	local pending = 0
	for key, def in Config.Passes do
		if MonetizationService.isConfigured("pass", key) then
			pending += 1
			task.spawn(function()
				local ok, has = pcall(function()
					return MarketplaceService:UserOwnsGamePassAsync(player.UserId, def.id)
				end)
				if ok and has and owned[player] then
					owned[player][key] = true
				end
				pending -= 1
			end)
		end
	end
	local deadline = os.clock() + 6
	while pending > 0 and os.clock() < deadline do
		task.wait(0.1)
	end
	checkSubscription(player)
	for key in Config.Passes do
		if MonetizationService.has(player, key) then
			applyPass(player, key)
		end
	end
	task.spawn(deliverGiftsTo, player)
end

function MonetizationService.init(services)
	S = services
	for key, def in Config.Products do
		if def.id > 0 then
			productKeyById[def.id] = key
		end
	end
	for key, def in Config.Passes do
		if def.id > 0 then
			passKeyById[def.id] = key
		end
	end
	MarketplaceService.ProcessReceipt = processReceipt

	MarketplaceService.PromptGamePassPurchaseFinished:Connect(function(player, passId, purchased)
		local key = passKeyById[passId]
		if not purchased or not key or not owned[player] then
			return
		end
		owned[player][key] = true
		applyPass(player, key)
		S.Analytics.custom(player, "BoughtPass_" .. key, priceOf("pass", key))
		S.PlayerService.fx(player, "celebrate", { text = Config.Passes[key].name .. "!" })
		S.PlayerService.notifyAll(string.format("🎉 %s got %s %s!", player.DisplayName, Config.Passes[key].icon,
			Config.Passes[key].name), "purchase")
		pushState(player)
		S.PlayerService.sync(player, S.EconomyService.state(player))
	end)

	Players.PlayerMembershipChanged:Connect(function(player)
		if S.PlayerService.isReady(player) then
			pushState(player)
		end
	end)
	Players.UserSubscriptionStatusChanged:Connect(function(player, subscriptionId)
		if subscriptionId == Config.SubscriptionId and S.PlayerService.isReady(player) then
			checkSubscription(player)
			S.PlayerService.refreshNametag(player)
			pushState(player)
		end
	end)

	S.PlayerService.onReady(onReady)
	S.PlayerService.onLeaving(function(player)
		owned[player] = nil
		club[player] = nil
		pendingGift[player] = nil
		pendingAd[player] = nil
		lastAd[player] = nil
	end)
	S.PlayerService.addState(function(player)
		return MonetizationService.state(player)
	end)

	S.PlayerService.registerAction("buyPass", function(player, key)
		return MonetizationService.promptPass(player, tostring(key))
	end)
	S.PlayerService.registerAction("buyProduct", function(player, key)
		key = tostring(key)
		local def = Config.Products[key]
		if not def or def.hidden or def.rarity or def.gift then
			return { ok = false }
		end
		return MonetizationService.promptProduct(player, key)
	end)
	S.PlayerService.registerAction("gift", function(player, payload)
		if type(payload) ~= "table" then
			return { ok = false }
		end
		local key = tostring(payload.key)
		local def = Config.Products[key]
		local target = Players:GetPlayerByUserId(tonumber(payload.target) or 0)
		if not def or not def.gift or not target or target == player then
			return { ok = false, msg = "Pick a player in this server." }
		end
		if MonetizationService.has(target, def.gift) then
			return { ok = false, msg = target.DisplayName .. " already has " .. Config.Passes[def.gift].name .. "!" }
		end
		local profile = profileOf(player)
		if (profile.data.giftCredits[def.gift] or 0) > 0 then
			local ok, err = sendGiftCredit(player, def.gift, target.UserId)
			return { ok = ok, msg = if ok then "🎁 Gift sent!" else err }
		end
		pendingGift[player] = { target = target.UserId, key = key }
		return MonetizationService.promptProduct(player, key)
	end)
	S.PlayerService.registerAction("watchAd", function(player, context)
		return MonetizationService.showAd(player, if context == "offline" then "offline" else "boost")
	end)
	S.PlayerService.registerAction("subscribe", function(player)
		if Config.SubscriptionId == "" then
			return { ok = false }
		end
		MarketplaceService:PromptSubscriptionPurchase(player, Config.SubscriptionId)
		return { ok = true }
	end)
	S.PlayerService.registerAction("premium", function(player)
		local ok = pcall(function()
			MarketplaceService:PromptRobloxSubscriptionPurchase(player)
		end)
		return { ok = ok }
	end)
end

function MonetizationService.start()
	-- real prices from Roblox so the shop always shows what players will pay
	for key, def in Config.Passes do
		if def.id > 0 then
			local ok, info = pcall(function()
				return MarketplaceService:GetProductInfoAsync(def.id, Enum.InfoType.GamePass)
			end)
			if ok and type(info) == "table" and type(info.PriceInRobux) == "number" then
				prices.passes[key] = info.PriceInRobux
			end
		end
	end
	for key, def in Config.Products do
		if def.id > 0 then
			local ok, info = pcall(function()
				return MarketplaceService:GetProductInfoAsync(def.id, Enum.InfoType.Product)
			end)
			if ok and type(info) == "table" and type(info.PriceInRobux) == "number" then
				prices.products[key] = info.PriceInRobux
			end
		end
	end
	for _, player in S.PlayerService.readyPlayers() do
		pushState(player)
	end
	-- pick up gifts sent while players are online in other servers
	while true do
		task.wait(90)
		for _, player in S.PlayerService.readyPlayers() do
			task.spawn(deliverGiftsTo, player)
			task.wait(0.5)
		end
	end
end

return MonetizationService
