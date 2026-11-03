-- Smaller panels: Codes (+ group join / invites), Settings, the offline
-- earnings popup, the Starter Pack offer and the admin panel.
local GroupService = game:GetService("GroupService")
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local SocialService = game:GetService("SocialService")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Config = require(Shared:WaitForChild("Config"))
local Format = require(Shared:WaitForChild("Format"))
local Rarities = require(Shared:WaitForChild("Rarities"))
local Client = script.Parent.Parent
local Net = require(Client:WaitForChild("Net"))
local Notify = require(Client:WaitForChild("Notify"))
local State = require(Client:WaitForChild("State"))
local Theme = require(Client:WaitForChild("Theme"))
local UIKit = require(Client:WaitForChild("UIKit"))

local C = Theme.Colors
local New = UIKit.New
local player = Players.LocalPlayer
local Misc = {}

local function report(result)
	if result.msg then
		Notify.show(result.msg, if result.ok then "success" else "warning")
	end
end

local function invite()
	local ok, canSend = pcall(function()
		return SocialService:CanSendGameInviteAsync(player)
	end)
	if ok and canSend then
		pcall(function()
			SocialService:PromptGameInvite(player)
		end)
	else
		Notify.show("Invites aren't available on this device.", "warning")
	end
end

local function buildCodes()
	local panel = UIKit.panel("Codes", "🔑 CODES", Vector2.new(600, 430))
	local c = panel.content
	UIKit.label({ Text = "Enter a code for free rewards!", Size = UDim2.new(1, 0, 0, 30), MaxTextSize = 24, Parent = c })
	local box = New("TextBox", {
		Name = "CodeBox",
		BackgroundColor3 = Color3.fromRGB(25, 20, 45),
		Position = UDim2.fromOffset(20, 44),
		Size = UDim2.new(1, -40, 0, 56),
		Font = Theme.Font,
		PlaceholderText = "TYPE CODE HERE",
		PlaceholderColor3 = C.SubText,
		Text = "",
		TextColor3 = C.Text,
		TextScaled = true,
		ClearTextOnFocus = false,
		Parent = c,
	}, { UIKit.corner(0.25), UIKit.stroke(3), New("UITextSizeConstraint", { MaxTextSize = 30 }) })
	UIKit.button({
		Text = "REDEEM",
		Color = C.Green,
		Size = UDim2.fromOffset(220, 54),
		Position = UDim2.new(0.5, 0, 0, 140),
		AnchorPoint = Vector2.new(0.5, 0.5),
		Parent = c,
		OnClick = function()
			local result = Net.call("redeem", box.Text)
			if not result.ok then
				Notify.show(result.msg or "Invalid code", "error")
			end
			box.Text = ""
		end,
	})
	local groupButton = UIKit.button({
		Text = "👥 Join Group (+" .. math.floor(Config.GroupBonus * 100) .. "% cash)",
		Color = C.Blue,
		Size = UDim2.new(1, -40, 0, 50),
		Position = UDim2.fromOffset(20, 190),
		MaxTextSize = 22,
		Parent = c,
		OnClick = function()
			if Config.GroupId > 0 then
				pcall(function()
					GroupService:PromptJoinAsync(Config.GroupId)
				end)
				report(Net.call("checkGroup"))
			end
		end,
	})
	groupButton.Visible = Config.GroupId > 0
	UIKit.button({
		Text = "📨 Invite Friends (+5% cash each!)",
		Color = C.Pink,
		Size = UDim2.new(1, -40, 0, 50),
		Position = UDim2.fromOffset(20, 250),
		MaxTextSize = 22,
		Parent = c,
		OnClick = invite,
	})
	UIKit.label({ Text = Config.SocialText, Size = UDim2.new(1, -40, 0, 26), Position = UDim2.fromOffset(20, 310),
		TextColor3 = C.SubText, MaxTextSize = 18, Parent = c })
	State.on("inGroup", function(inGroup)
		groupButton.Visible = Config.GroupId > 0 and not inGroup
	end)
end

local function buildSettings()
	local panel = UIKit.panel("Settings", "⚙️ SETTINGS", Vector2.new(560, 400))
	local c = panel.content
	UIKit.list(Enum.FillDirection.Vertical, 12).Parent = c
	local rows = {}
	-- music/sfx default ON, low graphics default OFF
	local function isOn(key: string, settings): boolean
		if key == "lowGfx" then
			return settings[key] == true
		end
		return settings[key] ~= false
	end
	local function toggle(key: string, text: string, order: number)
		local _, label = UIKit.button({
			Text = "",
			Color = C.Card,
			Size = UDim2.new(1, -20, 0, 56),
			LayoutOrder = order,
			MaxTextSize = 24,
			Parent = c,
			OnClick = function()
				local settings = table.clone(State.get("settings") or {})
				settings[key] = not isOn(key, settings)
				State.apply({ settings = settings })
				Net.call("settings", settings)
			end,
		})
		rows[key] = { label = label, text = text }
	end
	toggle("music", "🎵 Music", 1)
	toggle("sfx", "🔊 Sound effects", 2)
	toggle("lowGfx", "📱 Low graphics (faster phones)", 3)
	UIKit.button({ Text = "📨 Invite Friends", Color = C.Pink, Size = UDim2.new(1, -20, 0, 56), LayoutOrder = 4,
		MaxTextSize = 24, Parent = c, OnClick = invite })
	State.on("settings", function(settings)
		for key, row in rows do
			row.label.Text = row.text .. ":  " .. (if isOn(key, settings) then "ON ✅" else "OFF ❌")
		end
	end)
end

local function buildOffline()
	local panel = UIKit.panel("Offline", "🌙 WELCOME BACK!", Vector2.new(600, 400))
	local c = panel.content
	local text = UIKit.label({ Text = "", Size = UDim2.new(1, 0, 0, 90), TextWrapped = true, MaxTextSize = 28, Parent = c })
	local amountLabel = UIKit.label({ Text = "", Size = UDim2.new(1, 0, 0, 60), Position = UDim2.fromOffset(0, 92),
		TextColor3 = C.Cash, MaxTextSize = 48, StrokeThickness = 3, Parent = c })
	local buttons = New("Frame", {
		BackgroundTransparency = 1,
		Position = UDim2.fromOffset(0, 170),
		Size = UDim2.new(1, 0, 0, 140),
		Parent = c,
	}, { UIKit.list(Enum.FillDirection.Vertical, 10) })
	UIKit.button({ Text = "Collect", Color = C.Green, Size = UDim2.fromOffset(300, 50), LayoutOrder = 3, Parent = buttons,
		OnClick = function()
			Net.call("claimOffline", "normal")
			UIKit.close("Offline")
		end })
	local adButton = UIKit.button({ Text = "📺 Watch video: 2x", Color = C.Blue, Size = UDim2.fromOffset(300, 50),
		LayoutOrder = 1, Parent = buttons, OnClick = function()
			local result = Net.call("claimOffline", "ad")
			if result.ok then
				UIKit.close("Offline")
			else
				report(result)
			end
		end })
	local robuxButton, robuxLabel = UIKit.button({ Text = "", Color = C.Robux, Size = UDim2.fromOffset(300, 50),
		LayoutOrder = 2, Parent = buttons, OnClick = function()
			Net.call("claimOffline", "robux")
		end })
	State.on("offline", function(offline)
		if type(offline) ~= "table" then
			UIKit.close("Offline")
			return
		end
		local hours = math.floor(offline.seconds / 3600)
		local minutes = math.floor((offline.seconds % 3600) / 60)
		text.Text = string.format("Your slimes kept working while you were away (%dh %dm)!", hours, minutes)
		amountLabel.Text = "+" .. Format.cash(offline.amount)
		adButton.Visible = State.get("adsEnabled") == true
		local product = State.productInfo("OfflineDouble")
		robuxButton.Visible = product ~= nil
		if product then
			robuxLabel.Text = "2x for R$ " .. product.price
		end
		UIKit.open("Offline")
	end)
end

local function buildStarter()
	local panel = UIKit.panel("Starter", "🎁 STARTER PACK", Vector2.new(580, 440))
	local c = panel.content
	UIKit.list(Enum.FillDirection.Vertical, 10).Parent = c
	UIKit.label({ Text = "A one-time boost to get you going:", Size = UDim2.new(1, 0, 0, 30), MaxTextSize = 24,
		LayoutOrder = 1, Parent = c })
	for i, line in {
		"💰 30 minutes of your income (at least $25K)",
		"🧪 2x Cash for 1 hour",
		"🟢 Golden Neon Noodle (Epic) — protected from thieves for 10 min",
	} do
		UIKit.label({ Text = line, Size = UDim2.new(1, -20, 0, 34), TextColor3 = C.SubText, MaxTextSize = 22,
			LayoutOrder = 1 + i, Parent = c })
	end
	local _, buyLabel = UIKit.button({ Text = "", Color = C.Robux, Size = UDim2.fromOffset(300, 60), LayoutOrder = 10,
		MaxTextSize = 30, Parent = c, OnClick = function()
			report(Net.call("buyProduct", "StarterPack"))
		end })
	UIKit.label({ Text = "Only once per account.", Size = UDim2.new(1, 0, 0, 24), TextColor3 = C.SubText, MaxTextSize = 18,
		LayoutOrder = 11, Parent = c })
	panel.onOpen = function()
		local product = State.productInfo("StarterPack")
		buyLabel.Text = if product then "R$ " .. product.price else "—"
	end
	State.on("starterPackBought", function(bought)
		if bought then
			UIKit.close("Starter")
		end
	end)
	-- offer it once per session after the player has tried the game a bit
	task.delay(150, function()
		if State.productInfo("StarterPack") and State.get("starterPackBought") == false
			and (State.get("tutorial") or 0) >= 1 and not UIKit.isOpen("Offline") then
			UIKit.open("Starter")
		end
	end)
end

local function buildAdmin()
	local panel = UIKit.panel("Admin", "🛠️ ADMIN", Vector2.new(780, 520))
	local list = UIKit.scroll({ Size = UDim2.fromScale(1, 1), Parent = panel.content })
	UIKit.grid(Vector2.new(176, 52), 8).Parent = list
	local function add(text: string, color: Color3, payload)
		UIKit.button({ Text = text, Color = color, MaxTextSize = 18, Parent = list, OnClick = function()
			report(Net.call("admin", payload))
		end })
	end
	add("🍀 Luck x2 (10m)", C.Green, { cmd = "luck", mult = 2, minutes = 10 })
	add("🍀 Luck x5 (5m)", C.Green, { cmd = "luck", mult = 5, minutes = 5 })
	add("🍀 Luck x10 (5m)", C.Green, { cmd = "luck", mult = 10, minutes = 5 })
	add("🌧️ Rain (5m)", C.Blue, { cmd = "rain", minutes = 5 })
	add("✨ Golden (5m)", C.Gold, { cmd = "golden", minutes = 5 })
	for _, key in State.get("eventKeys") or { "LuckyHour", "GoldenHour", "SlimeRain", "GooMoon" } do
		add("▶ " .. key, C.Purple, { cmd = "event", key = key })
	end
	for _, rarity in Rarities.List do
		if rarity.rank >= 4 then
			add("Spawn " .. rarity.key, C.Pink, { cmd = "spawn", rarity = rarity.key, count = 1 })
		end
	end
	add("+$1M (test)", C.Card, { cmd = "cash", amount = 1e6 })
	add("+$1B (test)", C.Card, { cmd = "cash", amount = 1e9 })
	add("⚠️ Wipe MY data", C.Red, { cmd = "wipe" })
	local box = New("TextBox", {
		BackgroundColor3 = Color3.fromRGB(25, 20, 45),
		Size = UDim2.fromOffset(176, 52),
		Font = Theme.Font,
		PlaceholderText = "Announcement…",
		Text = "",
		TextColor3 = C.Text,
		TextScaled = true,
		ClearTextOnFocus = false,
		Parent = list,
	}, { UIKit.corner(0.25), UIKit.stroke(2) })
	UIKit.button({ Text = "📢 Announce", Color = C.Gold, MaxTextSize = 18, Parent = list, OnClick = function()
		report(Net.call("admin", { cmd = "announce", text = box.Text }))
		box.Text = ""
	end })
end

function Misc.init()
	buildCodes()
	buildSettings()
	buildOffline()
	buildStarter()
	buildAdmin()
end

return Misc
