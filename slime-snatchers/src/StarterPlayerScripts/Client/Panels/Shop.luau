-- Shop: passes, scaled cash packs (exact amount shown before buying), boosts
-- and gifting. Prices come from Roblox so they always match the purchase prompt.
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Config = require(Shared:WaitForChild("Config"))
local Economy = require(Shared:WaitForChild("Economy"))
local Format = require(Shared:WaitForChild("Format"))
local Client = script.Parent.Parent
local Net = require(Client:WaitForChild("Net"))
local Notify = require(Client:WaitForChild("Notify"))
local State = require(Client:WaitForChild("State"))
local Theme = require(Client:WaitForChild("Theme"))
local UIKit = require(Client:WaitForChild("UIKit"))

local C = Theme.Colors
local New = UIKit.New
local Shop = {}

local BOOST_TEXT = {
	ServerLuck = "🍀 2x luck for EVERYONE in the server for 15 min. Exact odds in 🎲 Odds.",
	ServerRain = "🌧️ Slimes spawn 2x faster for everyone for 10 min.",
	CashBoost = "🧪 2x cash for you for 30 min. Time stacks!",
	Shield = "🛡️ Nobody can steal from your base for 10 min.",
}

local function call(action: string, payload: any?)
	local result = Net.call(action, payload)
	if not result.ok and result.msg then
		Notify.show(result.msg, "warning")
	end
	return result
end

local function card(parent: Instance, order: number, icon: string, title: string, desc: string, buttonText: string,
	color: Color3, onClick: (() -> ())?)
	local frame = New("Frame", {
		Name = title,
		BackgroundColor3 = C.Card,
		LayoutOrder = order,
		Parent = parent,
	}, { UIKit.corner(0.1), UIKit.stroke(3) })
	UIKit.label({ Text = icon, Size = UDim2.new(1, 0, 0, 48), Position = UDim2.fromOffset(0, 8), StrokeThickness = 0,
		Parent = frame })
	UIKit.label({ Text = title, Size = UDim2.new(1, -16, 0, 28), Position = UDim2.fromOffset(8, 58), MaxTextSize = 24,
		Parent = frame })
	local descLabel = UIKit.label({
		Text = desc,
		Size = UDim2.new(1, -16, 0, 52),
		Position = UDim2.fromOffset(8, 88),
		TextColor3 = C.SubText,
		Font = Theme.FontBody,
		TextWrapped = true,
		MaxTextSize = 16,
		StrokeThickness = 0,
		Parent = frame,
	})
	UIKit.button({
		Text = buttonText,
		Color = color,
		Size = UDim2.new(1, -20, 0, 42),
		Position = UDim2.new(0, 10, 1, -50),
		MaxTextSize = 22,
		Parent = frame,
		OnClick = onClick,
	})
	return frame, descLabel
end

local function sortByOrder(list)
	local copy = table.clone(list)
	table.sort(copy, function(a, b)
		return (a.order or 99) < (b.order or 99)
	end)
	return copy
end

function Shop.init()
	local panel = UIKit.panel("Shop", "🛒 SHOP", Vector2.new(780, 520))
	local tab = "⭐ Passes"
	local giftTarget: number? = nil
	local cashLabels = {}

	local body = UIKit.scroll({
		Name = "Items",
		Position = UDim2.fromOffset(0, 56),
		Size = UDim2.new(1, 0, 1, -56),
		Parent = panel.content,
	})

	local function grid()
		UIKit.clear(body)
		local layout = UIKit.grid(Vector2.new(230, 204), 12)
		layout.Parent = body
		New("UIPadding", { PaddingTop = UDim.new(0, 4), PaddingBottom = UDim.new(0, 8), Parent = body })
	end

	local function renderPasses()
		grid()
		for i, pass in sortByOrder(State.get("passes") or {}) do
			local owned = State.has(pass.key)
			card(body, i, pass.icon, pass.name, pass.desc, if owned then "✓ OWNED" else "R$ " .. pass.price,
				if owned then C.Gray else C.Robux, if owned then nil else function()
					call("buyPass", pass.key)
				end)
		end
		if not State.get("isPremium") then
			card(body, 50, "⭐", "Roblox Plus", "Members get +" .. math.floor(Config.PremiumBonus * 100) .. "% cash in this game!",
				"Get Plus", C.Blue, function()
					call("premium")
				end)
		end
		if State.get("clubConfigured") and not State.get("isClub") then
			card(body, 51, "💎", Config.SubscriptionName, "+" .. math.floor(Config.SubscriptionBonus * 100)
				.. "% cash, [Club] tag, 8h offline cash. Monthly, cancel anytime.", "Subscribe", C.Purple, function()
				call("subscribe")
			end)
		end
	end

	local function refreshCash()
		local base = State.get("baseIncome") or 0
		for _, entry in cashLabels do
			entry.label.Text = "+" .. Format.cash(Economy.scaledCash(base, entry.minutes, entry.min))
				.. "\n(" .. entry.minutes .. " min of your income)"
		end
	end

	local function renderCash()
		grid()
		table.clear(cashLabels)
		for i, product in sortByOrder(State.get("products") or {}) do
			if product.minutes and product.min then
				local _, label = card(body, i, product.icon, product.name, "", "R$ " .. product.price, C.Robux, function()
					call("buyProduct", product.key)
				end)
				table.insert(cashLabels, { label = label, minutes = product.minutes, min = product.min })
			end
		end
		refreshCash()
	end

	local function renderBoosts()
		grid()
		local order = 0
		for _, product in sortByOrder(State.get("products") or {}) do
			if BOOST_TEXT[product.key] then
				order += 1
				card(body, order, product.icon, product.name, BOOST_TEXT[product.key], "R$ " .. product.price, C.Robux,
					function()
						call("buyProduct", product.key)
					end)
			end
		end
		if State.get("adsEnabled") then
			card(body, 90, "📺", "Free 2x Cash", "Watch a short video for " .. Config.RewardedAds.BoostMinutes
				.. " min of 2x cash.", "Watch", C.Blue, function()
				call("watchAd", "boost")
			end)
		end
	end

	local function renderGifts()
		UIKit.clear(body)
		UIKit.list(Enum.FillDirection.Vertical, 10).Parent = body
		UIKit.label({ Text = "1) Pick a player in this server:", Size = UDim2.new(1, 0, 0, 30), MaxTextSize = 22,
			LayoutOrder = 1, Parent = body })
		local row = New("Frame", {
			BackgroundTransparency = 1,
			Size = UDim2.new(1, 0, 0, 46),
			LayoutOrder = 2,
			Parent = body,
		}, { UIKit.list(Enum.FillDirection.Horizontal, 8, Enum.HorizontalAlignment.Left) })
		local others = 0
		for _, other in Players:GetPlayers() do
			if other ~= Players.LocalPlayer then
				others += 1
				UIKit.button({
					Text = other.DisplayName,
					Color = if giftTarget == other.UserId then C.Pink else C.Card,
					Size = UDim2.fromOffset(170, 44),
					MaxTextSize = 20,
					LayoutOrder = others,
					Parent = row,
					OnClick = function()
						giftTarget = other.UserId
						renderGifts()
					end,
				})
			end
		end
		if others == 0 then
			UIKit.label({ Text = "Invite a friend to gift them! (⚙️ Settings > Invite)", Size = UDim2.fromOffset(500, 40),
				TextColor3 = C.SubText, MaxTextSize = 20, Parent = row })
		end
		UIKit.label({ Text = "2) Choose a gift:", Size = UDim2.new(1, 0, 0, 30), MaxTextSize = 22, LayoutOrder = 3,
			Parent = body })
		local cards = New("Frame", {
			BackgroundTransparency = 1,
			Size = UDim2.new(1, 0, 0, 216),
			LayoutOrder = 4,
			Parent = body,
		}, { UIKit.grid(Vector2.new(230, 204), 12) })
		local credits = State.get("giftCredits") or {}
		for i, product in sortByOrder(State.get("products") or {}) do
			if product.gift then
				local credit = credits[product.gift] or 0
				local pass = Config.Passes[product.gift]
				card(cards, i, "🎁", pass.name, "Give " .. pass.name .. " to a friend, even if they're offline later.",
					if credit > 0 then ("Send (" .. credit .. " ready)") else "R$ " .. product.price,
					if credit > 0 then C.Pink else C.Robux, function()
						if not giftTarget then
							Notify.show("Pick a player first!", "warning")
							return
						end
						local result = call("gift", { key = product.key, target = giftTarget })
						if result.ok and result.msg then
							Notify.show(result.msg, "success")
						end
					end)
			end
		end
	end

	local renderers = {
		["⭐ Passes"] = renderPasses,
		["💵 Cash"] = renderCash,
		["⚡ Boosts"] = renderBoosts,
		["🎁 Gifts"] = renderGifts,
	}
	local function render()
		renderers[tab]()
	end
	local select = UIKit.tabs(panel.content, { "⭐ Passes", "💵 Cash", "⚡ Boosts", "🎁 Gifts" }, function(name)
		tab = name
		render()
	end)
	panel.onOpen = render
	for _, key in { "passes", "products", "owned", "gifted", "giftCredits", "isPremium", "isClub" } do
		State.on(key, function()
			if UIKit.isOpen("Shop") then
				render()
			end
		end)
	end
	State.on("baseIncome", function()
		if UIKit.isOpen("Shop") and tab == "💵 Cash" then
			refreshCash()
		end
	end)
	select("⭐ Passes")
end

return Shop
