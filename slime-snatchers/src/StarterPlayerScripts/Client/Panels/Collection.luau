-- "Slimes" (your base: sell / fuse) and "Index" (collection book) panels.
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Config = require(Shared:WaitForChild("Config"))
local Economy = require(Shared:WaitForChild("Economy"))
local Format = require(Shared:WaitForChild("Format"))
local Mutations = require(Shared:WaitForChild("Mutations"))
local Rarities = require(Shared:WaitForChild("Rarities"))
local Slimes = require(Shared:WaitForChild("Slimes"))
local Client = script.Parent.Parent
local Net = require(Client:WaitForChild("Net"))
local Notify = require(Client:WaitForChild("Notify"))
local State = require(Client:WaitForChild("State"))
local Theme = require(Client:WaitForChild("Theme"))
local UIKit = require(Client:WaitForChild("UIKit"))

local C = Theme.Colors
local New = UIKit.New
local Collection = {}

local function slimeLabel(slime): string
	local def = Slimes.ById[slime.id]
	return (if slime.mut ~= "None" then slime.mut .. " " else "") .. def.name
end

local function buildSlimesPanel()
	local panel = UIKit.panel("Slimes", "🟢 MY SLIMES", Vector2.new(780, 520))
	local header = UIKit.label({ Text = "", Size = UDim2.new(1, 0, 0, 28), TextColor3 = C.SubText, MaxTextSize = 20,
		Parent = panel.content })
	local list = UIKit.scroll({ Position = UDim2.fromOffset(0, 34), Size = UDim2.new(1, 0, 1, -34), Parent = panel.content })
	local confirmUid: string? = nil

	local function render()
		UIKit.clear(list)
		UIKit.list(Enum.FillDirection.Vertical, 8).Parent = list
		local slimes = table.clone(State.get("slimes") or {})
		table.sort(slimes, function(a, b)
			return Economy.slimeIncome(a) > Economy.slimeIncome(b)
		end)
		header.Text = string.format("%d / %d pads used   ·   3 identical slimes = Fuse into a better mutation!",
			#slimes, State.get("maxPads") or Config.Tuning.BasePads)

		-- fusion offers
		local counts, order = {}, 0
		for _, slime in slimes do
			counts[slime.id] = (counts[slime.id] or 0) + 1
		end
		for id, count in counts do
			if count >= 3 then
				order += 1
				local def = Slimes.ById[id]
				local row = New("Frame", {
					BackgroundColor3 = C.Purple,
					Size = UDim2.new(1, -12, 0, 64),
					LayoutOrder = order,
					Parent = list,
				}, { UIKit.corner(0.2), UIKit.stroke(3, C.Gold) })
				local icon = UIKit.slimeIcon(id, "None", UDim2.fromOffset(60, 50), true)
				icon.Position = UDim2.fromOffset(10, 7)
				icon.Parent = row
				UIKit.label({ Text = "✨ Fuse 3x " .. def.name .. " → upgraded mutation!", Size = UDim2.new(0.62, 0, 0.7, 0),
					Position = UDim2.fromOffset(84, 10), TextXAlignment = Enum.TextXAlignment.Left, MaxTextSize = 22,
					Parent = row })
				UIKit.button({
					Text = "FUSE",
					Color = C.Gold,
					Size = UDim2.fromOffset(130, 46),
					Position = UDim2.new(1, -140, 0.5, 0),
					AnchorPoint = Vector2.new(0, 0.5),
					Parent = row,
					OnClick = function()
						local result = Net.call("fuse", id)
						if not result.ok and result.msg then
							Notify.show(result.msg, "warning")
						end
					end,
				})
			end
		end

		for i, slime in slimes do
			local def = Slimes.ById[slime.id]
			local rarity = Rarities.ByKey[def.rarity]
			local row = New("Frame", {
				BackgroundColor3 = C.Card,
				Size = UDim2.new(1, -12, 0, 70),
				LayoutOrder = 100 + i,
				Parent = list,
			}, { UIKit.corner(0.2), UIKit.stroke(3, rarity.color) })
			local icon = UIKit.slimeIcon(slime.id, slime.mut, UDim2.fromOffset(64, 54), true)
			icon.Position = UDim2.fromOffset(10, 8)
			icon.Parent = row
			UIKit.label({ Text = slimeLabel(slime), Size = UDim2.new(0.42, 0, 0, 30), Position = UDim2.fromOffset(86, 6),
				TextXAlignment = Enum.TextXAlignment.Left, TextColor3 = Mutations.ByKey[slime.mut].color, MaxTextSize = 24,
				Parent = row })
			UIKit.label({
				Text = string.format("%s · Lv %d%s", def.rarity, slime.lvl, if slime.lvl >= Economy.LEVEL_MAX then " MAX" else ""),
				Size = UDim2.new(0.42, 0, 0, 24),
				Position = UDim2.fromOffset(86, 38),
				TextXAlignment = Enum.TextXAlignment.Left,
				TextColor3 = rarity.color,
				MaxTextSize = 18,
				Parent = row,
			})
			UIKit.label({ Text = Format.rate(Economy.slimeIncome(slime)), Size = UDim2.new(0.2, 0, 0, 30),
				Position = UDim2.new(0.55, 0, 0.5, 0), AnchorPoint = Vector2.new(0, 0.5), TextColor3 = C.Cash, MaxTextSize = 22,
				Parent = row })
			local confirming = confirmUid == slime.uid
			UIKit.button({
				Text = if confirming then "Sure?" else "Sell " .. Format.cash(Economy.sellValue(slime)),
				Color = if confirming then C.Red else C.PanelLight,
				Size = UDim2.fromOffset(150, 44),
				Position = UDim2.new(1, -160, 0.5, 0),
				AnchorPoint = Vector2.new(0, 0.5),
				MaxTextSize = 20,
				Parent = row,
				OnClick = function()
					if rarity.rank >= Rarities.ByKey.Epic.rank and confirmUid ~= slime.uid then
						confirmUid = slime.uid
						render()
						return
					end
					confirmUid = nil
					Net.call("sell", slime.uid)
				end,
			})
		end
	end
	panel.onOpen = function()
		confirmUid = nil
		render()
	end
	State.on("slimes", function()
		if UIKit.isOpen("Slimes") then
			render()
		end
	end)
end

local function buildIndexPanel()
	local panel = UIKit.panel("Index", "📖 SLIME INDEX", Vector2.new(800, 520))
	local header = UIKit.label({ Text = "", Size = UDim2.new(1, 0, 0, 30), MaxTextSize = 22, Parent = panel.content })
	local grid = UIKit.scroll({ Position = UDim2.fromOffset(0, 38), Size = UDim2.new(1, 0, 1, -38), Parent = panel.content })

	local function render()
		UIKit.clear(grid)
		UIKit.grid(Vector2.new(140, 168), 10).Parent = grid
		local index = State.get("index") or {}
		local found = 0
		for _, def in Slimes.List do
			local entry = index[def.id]
			local known = entry ~= nil
			if known then
				found += 1
			end
			local rarity = Rarities.ByKey[def.rarity]
			local card = New("Frame", {
				BackgroundColor3 = if known then C.Card else C.PanelLight,
				LayoutOrder = def.index,
				Parent = grid,
			}, { UIKit.corner(0.12), UIKit.stroke(3, if known then rarity.color else C.Stroke) })
			local icon = UIKit.slimeIcon(def.id, "None", UDim2.fromOffset(76, 63), known)
			icon.AnchorPoint = Vector2.new(0.5, 0)
			icon.Position = UDim2.new(0.5, 0, 0, 10)
			icon.Parent = card
			UIKit.label({ Text = if known then def.name else "???", Size = UDim2.new(1, -10, 0, 26),
				Position = UDim2.fromOffset(5, 80), MaxTextSize = 20, Parent = card })
			UIKit.label({ Text = def.rarity, Size = UDim2.new(1, -10, 0, 20), Position = UDim2.fromOffset(5, 106),
				TextColor3 = rarity.color, MaxTextSize = 16, Parent = card })
			-- mutation dots
			local dots = New("Frame", {
				BackgroundTransparency = 1,
				Size = UDim2.new(1, -10, 0, 22),
				Position = UDim2.fromOffset(5, 134),
				Parent = card,
			}, { UIKit.list(Enum.FillDirection.Horizontal, 4) })
			for _, mut in Mutations.List do
				local has = entry ~= nil and entry[mut.key] == true
				New("Frame", {
					BackgroundColor3 = if has then mut.color else Color3.fromRGB(40, 35, 60),
					Size = UDim2.fromOffset(18, 18),
					LayoutOrder = mut.rank,
					Parent = dots,
				}, { UIKit.corner(0.5), UIKit.stroke(1) })
			end
		end
		header.Text = string.format("Discovered %d / %d   ·   +%d%% cash bonus   ·   dots = mutations found", found,
			#Slimes.List, math.floor(found * Config.Tuning.IndexBonusEach * 100 + 0.5))
	end
	panel.onOpen = render
	State.on("index", function()
		if UIKit.isOpen("Index") then
			render()
		end
	end)
end

function Collection.init()
	buildSlimesPanel()
	buildIndexPanel()
end

return Collection
