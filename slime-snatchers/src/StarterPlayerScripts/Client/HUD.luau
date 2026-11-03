-- Main HUD: cash counter, menu buttons (left), offers (right), event/boost
-- chips and the carry banner (bottom).
local AdService = game:GetService("AdService")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService = game:GetService("RunService")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Format = require(Shared:WaitForChild("Format"))
local RewardDefs = require(Shared:WaitForChild("RewardDefs"))
local Net = require(script.Parent:WaitForChild("Net"))
local Notify = require(script.Parent:WaitForChild("Notify"))
local State = require(script.Parent:WaitForChild("State"))
local Theme = require(script.Parent:WaitForChild("Theme"))
local UIKit = require(script.Parent:WaitForChild("UIKit"))

local C = Theme.Colors
local New = UIKit.New
local HUD = {}

local adAvailable = false
local badges = {}
local offerButtons = {}

local function serverNow(): number
	return workspace:GetServerTimeNow()
end

local function menuButton(parent: Instance, icon: string, caption: string, color: Color3, order: number, onClick)
	local button = UIKit.button({
		Name = caption,
		Text = "",
		Color = color,
		Size = UDim2.fromOffset(80, 80),
		LayoutOrder = order,
		Radius = 0.3,
		Parent = parent,
		OnClick = onClick,
	})
	UIKit.label({
		Text = icon,
		Size = UDim2.fromScale(0.8, 0.55),
		Position = UDim2.fromScale(0.1, 0.06),
		StrokeThickness = 0,
		Parent = button,
	})
	UIKit.label({
		Text = caption,
		Size = UDim2.fromScale(0.94, 0.3),
		Position = UDim2.fromScale(0.03, 0.64),
		MaxTextSize = 18,
		Parent = button,
	})
	local badge = New("Frame", {
		Name = "Badge",
		BackgroundColor3 = C.Red,
		Size = UDim2.fromOffset(22, 22),
		Position = UDim2.new(1, -8, 0, -6),
		Visible = false,
		ZIndex = 5,
		Parent = button,
	}, { UIKit.corner(0.5), UIKit.stroke(2) })
	UIKit.label({ Text = "!", Size = UDim2.fromScale(1, 1), ZIndex = 6, Parent = badge })
	badges[caption] = badge
	return button
end

local function pulse(button: GuiObject)
	local scale = New("UIScale", { Name = "Pulse", Parent = button })
	task.spawn(function()
		while button.Parent do
			UIKit.tween(scale, 0.6, { Scale = 1.08 }, Enum.EasingStyle.Sine)
			task.wait(0.6)
			UIKit.tween(scale, 0.6, { Scale = 1 }, Enum.EasingStyle.Sine)
			task.wait(0.6)
		end
	end)
end

local function offerButton(parent: Instance, key: string, icon: string, caption: string, color: Color3, order: number, onClick)
	local button = UIKit.button({
		Name = key,
		Text = "",
		Color = color,
		Size = UDim2.fromOffset(84, 84),
		LayoutOrder = order,
		Radius = 0.5,
		Parent = parent,
		OnClick = onClick,
	})
	button.Visible = false
	UIKit.label({ Text = icon, Size = UDim2.fromScale(0.7, 0.5), Position = UDim2.fromScale(0.15, 0.1), StrokeThickness = 0,
		Parent = button })
	UIKit.label({ Text = caption, Size = UDim2.fromScale(0.9, 0.28), Position = UDim2.fromScale(0.05, 0.6), MaxTextSize = 18,
		Parent = button })
	pulse(button)
	offerButtons[key] = button
	return button
end

local function checkAds()
	local ok, result = pcall(function()
		return AdService:GetAdAvailabilityNowAsync(Enum.AdFormat.RewardedVideo)
	end)
	if ok and type(result) == "table" then
		adAvailable = false
		for _, value in result do
			if value == Enum.AdAvailabilityResult.IsAvailable then
				adAvailable = true
			end
		end
	else
		adAvailable = true -- unknown on this client; the server reports if no ad is available
	end
end

local function refreshOffers()
	local function show(key: string, visible: boolean)
		if offerButtons[key] then
			offerButtons[key].Visible = visible
		end
	end
	show("VIP", State.passInfo("VIP") ~= nil and not State.has("VIP"))
	show("DoubleCash", State.passInfo("DoubleCash") ~= nil and not State.has("DoubleCash"))
	show("StarterPack", State.productInfo("StarterPack") ~= nil and State.get("starterPackBought") == false)
	show("FreeAd", State.get("adsEnabled") == true and adAvailable and serverNow() >= (State.get("adReadyAt") or 0))
	show("Club", State.get("clubConfigured") == true and State.get("isClub") == false)
end

local function refreshBadges()
	local daily = State.get("daily")
	local ready = daily ~= nil and daily.canClaim == true
	for _, quest in State.get("quests") or {} do
		if quest.done and not quest.claimed then
			ready = true
		end
	end
	local gifts = State.get("gifts")
	if gifts then
		local elapsed = serverNow() - gifts.startedAt
		local claimed = {}
		for _, i in gifts.claimed do
			claimed[i] = true
		end
		for i, gift in RewardDefs.Gifts do
			if not claimed[i] and elapsed >= gift.at * 60 then
				ready = true
			end
		end
	end
	badges.Rewards.Visible = ready
	local slimes = State.get("slimes") or {}
	local counts = {}
	local fusable = false
	for _, slime in slimes do
		counts[slime.id] = (counts[slime.id] or 0) + 1
		if counts[slime.id] >= 3 then
			fusable = true
		end
	end
	badges.Slimes.Visible = fusable
end

local function chip(parent: Instance, name: string, color: Color3, order: number)
	local frame = New("Frame", {
		Name = name,
		BackgroundColor3 = color,
		Size = UDim2.fromOffset(150, 34),
		LayoutOrder = order,
		Visible = false,
		Parent = parent,
	}, { UIKit.corner(0.5), UIKit.stroke(2) })
	local label = UIKit.label({ Text = "", Size = UDim2.new(1, -12, 0.8, 0), Position = UDim2.fromScale(0.5, 0.5),
		AnchorPoint = Vector2.new(0.5, 0.5), MaxTextSize = 20, Parent = frame })
	return frame, label
end

function HUD.init(gui: ScreenGui, open: (string) -> ())
	-- cash ---------------------------------------------------------------
	local top = New("Frame", {
		Name = "Top",
		AnchorPoint = Vector2.new(0.5, 0),
		Position = UDim2.new(0.5, 0, 0, 6),
		Size = UDim2.fromOffset(360, 96),
		BackgroundTransparency = 1,
		Parent = gui,
	})
	UIKit.scaled(top)
	local pill = New("Frame", {
		Name = "Cash",
		BackgroundColor3 = C.Panel,
		Size = UDim2.fromOffset(360, 62),
		Parent = top,
	}, { UIKit.corner(0.5), UIKit.stroke(4), UIKit.gradient(Color3.new(1, 1, 1), Color3.fromRGB(190, 180, 220)) })
	UIKit.label({ Text = "💰", Size = UDim2.fromOffset(52, 46), Position = UDim2.fromOffset(12, 8), StrokeThickness = 0,
		Parent = pill })
	local cashLabel = UIKit.label({
		Text = "$0",
		Size = UDim2.new(1, -86, 0.8, 0),
		Position = UDim2.fromOffset(70, 6),
		TextColor3 = C.Cash,
		TextXAlignment = Enum.TextXAlignment.Left,
		MaxTextSize = 42,
		StrokeThickness = 3,
		Parent = pill,
	})
	local incomeLabel = UIKit.label({
		Text = "",
		Size = UDim2.fromOffset(360, 28),
		Position = UDim2.fromOffset(0, 66),
		TextColor3 = C.SubText,
		MaxTextSize = 22,
		Parent = top,
	})
	local shown = 0
	RunService.RenderStepped:Connect(function(dt)
		local target = State.get("cash") or 0
		if target < shown or math.abs(target - shown) < 1 then
			shown = target
		else
			shown += (target - shown) * math.min(1, dt * 7)
		end
		cashLabel.Text = Format.cash(shown)
	end)
	local function refreshIncome()
		incomeLabel.Text = "+" .. Format.rate(State.get("income") or 0) .. "   ·   " .. Format.mult(State.get("mult") or 1)
	end
	State.on("income", refreshIncome)
	State.on("mult", refreshIncome)

	-- menu -----------------------------------------------------------------
	local menu = New("Frame", {
		Name = "Menu",
		AnchorPoint = Vector2.new(0, 0.5),
		Position = UDim2.new(0, 10, 0.5, 10),
		Size = UDim2.fromOffset(176, 360),
		BackgroundTransparency = 1,
		Parent = gui,
	}, { UIKit.grid(Vector2.new(80, 80), 8) })
	UIKit.scaled(menu)
	local entries = {
		{ "🛒", "Shop", C.Gold },
		{ "🎁", "Rewards", C.Pink },
		{ "🟢", "Slimes", C.Green },
		{ "📖", "Index", C.Blue },
		{ "⭐", "Rebirth", C.Purple },
		{ "🎲", "Odds", C.Card },
		{ "🔑", "Codes", C.Card },
		{ "⚙️", "Settings", C.Card },
	}
	for i, entry in entries do
		menuButton(menu, entry[1], entry[2], entry[3], i, function()
			open(entry[2])
		end)
	end

	-- offers -----------------------------------------------------------------
	local offers = New("Frame", {
		Name = "Offers",
		AnchorPoint = Vector2.new(1, 0.5),
		Position = UDim2.new(1, -10, 0.5, 10),
		Size = UDim2.fromOffset(90, 470),
		BackgroundTransparency = 1,
		Parent = gui,
	}, { UIKit.list(Enum.FillDirection.Vertical, 10) })
	UIKit.scaled(offers)
	offerButton(offers, "VIP", "👑", "VIP", C.Gold, 1, function()
		Net.call("buyPass", "VIP")
	end)
	offerButton(offers, "DoubleCash", "💰", "2x Cash", C.Green, 2, function()
		Net.call("buyPass", "DoubleCash")
	end)
	offerButton(offers, "StarterPack", "🎁", "Starter", C.Pink, 3, function()
		open("Starter")
	end)
	offerButton(offers, "FreeAd", "📺", "FREE 2x", C.Blue, 4, function()
		local result = Net.call("watchAd", "boost")
		if not result.ok and result.msg then
			Notify.show(result.msg, "warning")
		end
		task.delay(2, checkAds)
	end)
	offerButton(offers, "Club", "💎", "Club", C.Purple, 5, function()
		Net.call("subscribe")
	end)
	for _, key in { "passes", "owned", "gifted", "starterPackBought", "adsEnabled", "adReadyAt", "isClub", "products" } do
		State.on(key, refreshOffers)
	end

	-- bottom -------------------------------------------------------------------
	local bottom = New("Frame", {
		Name = "Bottom",
		AnchorPoint = Vector2.new(0.5, 1),
		Position = UDim2.new(0.5, 0, 1, -8),
		Size = UDim2.fromOffset(660, 150),
		BackgroundTransparency = 1,
		Parent = gui,
	}, { UIKit.list(Enum.FillDirection.Vertical, 6, nil, Enum.VerticalAlignment.Bottom) })
	UIKit.scaled(bottom)
	local carry = New("Frame", {
		Name = "Carry",
		BackgroundColor3 = C.Red,
		Size = UDim2.fromOffset(520, 40),
		LayoutOrder = 1,
		Visible = false,
		Parent = bottom,
	}, { UIKit.corner(0.5), UIKit.stroke(3) })
	local carryLabel = UIKit.label({ Text = "", Size = UDim2.new(1, -20, 0.8, 0), Position = UDim2.fromScale(0.5, 0.5),
		AnchorPoint = Vector2.new(0.5, 0.5), MaxTextSize = 24, Parent = carry })
	State.on("carrying", function(info)
		carry.Visible = type(info) == "table"
		if type(info) == "table" then
			carryLabel.Text = "🏃 Carrying " .. info.name .. " — run to YOUR base!"
		end
	end)

	local banner = New("Frame", {
		Name = "Event",
		BackgroundColor3 = C.Purple,
		Size = UDim2.fromOffset(460, 40),
		LayoutOrder = 2,
		Visible = false,
		Parent = bottom,
	}, { UIKit.corner(0.5), UIKit.stroke(3) })
	local bannerLabel = UIKit.label({ Text = "", Size = UDim2.new(1, -20, 0.8, 0), Position = UDim2.fromScale(0.5, 0.5),
		AnchorPoint = Vector2.new(0.5, 0.5), MaxTextSize = 24, Parent = banner })

	local chips = New("Frame", {
		Name = "Chips",
		BackgroundTransparency = 1,
		Size = UDim2.fromOffset(660, 34),
		LayoutOrder = 3,
		Parent = bottom,
	}, { UIKit.list(Enum.FillDirection.Horizontal, 6) })
	local lockChip, lockLabel = chip(chips, "Lock", C.Red, 1)
	local shieldChip, shieldLabel = chip(chips, "Shield", C.Blue, 2)
	local potionChip, potionLabel = chip(chips, "Potion", C.Green, 3)
	local luckChip, luckLabel = chip(chips, "Luck", Color3.fromRGB(60, 170, 90), 4)
	local rainChip, rainLabel = chip(chips, "Rain", Color3.fromRGB(70, 120, 220), 5)
	local goldenChip, goldenLabel = chip(chips, "Golden", Color3.fromRGB(200, 150, 30), 6)

	local serverState = ReplicatedStorage:WaitForChild("ServerState")
	task.spawn(function()
		local lastAdCheck = 0
		while true do
			local t = serverNow()
			local eventEnds = serverState:GetAttribute("EventEnds") or 0
			local eventName = serverState:GetAttribute("EventName") or ""
			banner.Visible = eventName ~= "" and eventEnds > t
			if banner.Visible then
				bannerLabel.Text = eventName .. "  " .. Format.time(eventEnds - t)
			end
			local function timed(frame: Frame, label: TextLabel, ends: number, text: string)
				frame.Visible = ends > t
				if frame.Visible then
					label.Text = text .. " " .. Format.time(ends - t)
				end
			end
			timed(lockChip, lockLabel, State.get("lockedUntil") or 0, "🔒")
			timed(luckChip, luckLabel, serverState:GetAttribute("LuckEnds") or 0,
				"🍀 x" .. tostring(serverState:GetAttribute("LuckMult") or 2))
			timed(rainChip, rainLabel, serverState:GetAttribute("RainEnds") or 0, "🌧️")
			timed(goldenChip, goldenLabel, serverState:GetAttribute("GoldenEnds") or 0, "✨")
			local boosts = State.get("boosts") or {}
			timed(shieldChip, shieldLabel, t + (boosts.shield or 0), "🛡️")
			timed(potionChip, potionLabel, t + (boosts.cash2x or 0), "🧪 2x")
			if os.clock() - lastAdCheck > 60 then
				lastAdCheck = os.clock()
				task.spawn(checkAds)
			end
			refreshOffers()
			refreshBadges()
			task.wait(0.25)
		end
	end)

	-- admin --------------------------------------------------------------------
	local adminButton = UIKit.button({
		Name = "Admin",
		Text = "🛠️ Admin",
		Color = C.Red,
		Size = UDim2.fromOffset(120, 40),
		Position = UDim2.new(0, 10, 0, 50),
		MaxTextSize = 20,
		Parent = gui,
		OnClick = function()
			open("Admin")
		end,
	})
	adminButton.Visible = false
	UIKit.scaled(adminButton)
	State.on("isAdmin", function(isAdmin)
		adminButton.Visible = isAdmin == true
	end)
	State.on("memoryMode", function(memory)
		if memory then
			Notify.show("⚠️ Studio test mode: data is NOT saved (enable API Services to test saving).", "warning")
		end
	end)
end

return HUD
