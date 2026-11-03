-- Rebirth panel and the Odds panel (every chance shown as a percentage,
-- including the effect of active luck boosts).
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Config = require(Shared:WaitForChild("Config"))
local Economy = require(Shared:WaitForChild("Economy"))
local Format = require(Shared:WaitForChild("Format"))
local Mutations = require(Shared:WaitForChild("Mutations"))
local Rarities = require(Shared:WaitForChild("Rarities"))
local Client = script.Parent.Parent
local Net = require(Client:WaitForChild("Net"))
local Notify = require(Client:WaitForChild("Notify"))
local State = require(Client:WaitForChild("State"))
local Theme = require(Client:WaitForChild("Theme"))
local UIKit = require(Client:WaitForChild("UIKit"))

local C = Theme.Colors
local New = UIKit.New
local Progress = {}

local function buildRebirth()
	local panel = UIKit.panel("Rebirth", "⭐ REBIRTH", Vector2.new(640, 470))
	local c = panel.content
	local title = UIKit.label({ Text = "", Size = UDim2.new(1, 0, 0, 40), MaxTextSize = 34, Parent = c })
	local cost = UIKit.label({ Text = "", Size = UDim2.new(1, 0, 0, 30), Position = UDim2.fromOffset(0, 44),
		TextColor3 = C.Gold, MaxTextSize = 26, Parent = c })
	local bar = UIKit.progress(c, UDim2.new(1, -40, 0, 30), C.Gold)
	bar.frame.Position = UDim2.fromOffset(20, 84)
	local perks = New("Frame", {
		BackgroundColor3 = C.Card,
		Position = UDim2.fromOffset(20, 128),
		Size = UDim2.new(1, -40, 0, 150),
		Parent = c,
	}, { UIKit.corner(0.1), UIKit.stroke(3), UIKit.padding(10), UIKit.list(Enum.FillDirection.Vertical, 6) })
	local perkLabels = {}
	for i = 1, 4 do
		perkLabels[i] = UIKit.label({ Text = "", Size = UDim2.new(1, 0, 0, 28), TextXAlignment = Enum.TextXAlignment.Left,
			MaxTextSize = 22, LayoutOrder = i, Parent = perks })
	end
	local confirming = false
	local button, buttonLabel = UIKit.button({
		Text = "REBIRTH",
		Color = C.Gray,
		Size = UDim2.fromOffset(260, 60),
		Position = UDim2.new(0.5, 0, 1, -40),
		AnchorPoint = Vector2.new(0.5, 0.5),
		MaxTextSize = 32,
		Parent = c,
		OnClick = function()
			local rebirths = State.get("rebirths") or 0
			if (State.get("cash") or 0) < Economy.rebirthCost(rebirths) then
				Notify.show("Not enough cash yet!", "warning")
				return
			end
			if not confirming then
				confirming = true
				return
			end
			confirming = false
			local result = Net.call("rebirth")
			if not result.ok and result.msg then
				Notify.show(result.msg, "warning")
			end
		end,
	})

	local function refresh()
		local rebirths = State.get("rebirths") or 0
		local cash = State.get("cash") or 0
		local need = Economy.rebirthCost(rebirths)
		title.Text = "Rebirth " .. rebirths .. "  →  " .. rebirths + 1
		cost.Text = "Cost: " .. Format.cash(need)
		bar.set(cash / need, Format.cash(cash) .. " / " .. Format.cash(need))
		local padsNow = math.min(rebirths * Config.Tuning.PadsPerRebirth, Config.Tuning.MaxRebirthPads)
		local padsNext = math.min((rebirths + 1) * Config.Tuning.PadsPerRebirth, Config.Tuning.MaxRebirthPads)
		perkLabels[1].Text = "💰 Cash multiplier: " .. Format.mult(Economy.rebirthMult(rebirths)) .. " → "
			.. Format.mult(Economy.rebirthMult(rebirths + 1))
		perkLabels[2].Text = if padsNext > padsNow then "🧱 +" .. (padsNext - padsNow) .. " slime pads" else "🧱 Max rebirth pads reached"
		perkLabels[3].Text = "🟢 Your slimes STAY in your base!"
		perkLabels[4].Text = "⚠️ Your cash resets to " .. Format.cash(Config.Tuning.StartingCash)
		local affordable = cash >= need
		button.BackgroundColor3 = if affordable then (if confirming then C.Red else C.Green) else C.Gray
		buttonLabel.Text = if confirming then "TAP AGAIN TO CONFIRM" else "REBIRTH"
	end
	panel.onOpen = function()
		confirming = false
		refresh()
	end
	State.on("cash", function()
		if UIKit.isOpen("Rebirth") then
			refresh()
		end
	end)
	State.on("rebirths", function()
		if UIKit.isOpen("Rebirth") then
			refresh()
		end
	end)
end

local function buildOdds()
	local panel = UIKit.panel("Odds", "🎲 SPAWN ODDS", Vector2.new(720, 520))
	local list = UIKit.scroll({ Size = UDim2.fromScale(1, 1), Parent = panel.content })
	local serverState = ReplicatedStorage:WaitForChild("ServerState")

	local function row(order: number, cells: { string }, color: Color3?, header: boolean?)
		local frame = New("Frame", {
			BackgroundColor3 = if header then C.PanelLight else C.Card,
			Size = UDim2.new(1, -12, 0, 34),
			LayoutOrder = order,
			Parent = list,
		}, { UIKit.corner(0.25), UIKit.list(Enum.FillDirection.Horizontal, 0) })
		for i, text in cells do
			UIKit.label({
				Text = text,
				Size = UDim2.new(1 / #cells, 0, 1, -6),
				TextColor3 = if i == 1 and color then color else C.Text,
				MaxTextSize = 20,
				LayoutOrder = i,
				Parent = frame,
			})
		end
	end

	local function render()
		UIKit.clear(list)
		UIKit.list(Enum.FillDirection.Vertical, 4).Parent = list
		local luck = serverState:GetAttribute("LuckMult") or 1
		local mutMult = serverState:GetAttribute("MutationMult") or 1
		local base = Economy.rarityOdds(1)
		local now = Economy.rarityOdds(luck)
		UIKit.label({ Text = "Server luck now: " .. Format.mult(luck) .. "   ·   Mutation chance: " .. Format.mult(mutMult),
			Size = UDim2.new(1, -12, 0, 30), TextColor3 = C.Gold, MaxTextSize = 22, LayoutOrder = 0, Parent = list })
		row(1, { "Rarity", "Base chance", "Chance now" }, nil, true)
		for _, def in Rarities.List do
			row(1 + def.rank, { def.key, Format.percent(base[def.key]), Format.percent(now[def.key]) }, def.color)
		end
		row(20, { "Mutation", "Cash bonus", "Chance now" }, nil, true)
		local mutOdds = Economy.mutationOdds(mutMult)
		for _, mut in Mutations.List do
			row(20 + mut.rank, { if mut.key == "None" then "No mutation" else mut.key, Format.mult(mut.mult),
				Format.percent(mutOdds[mut.key]) }, mut.color)
		end
		local notes = {
			"• Which slime spawns within a rarity: cheaper ones are more common.",
			"• Guarantee: an Epic or better slime spawns at least every " .. Config.Tuning.GuaranteeEpicSeconds .. " seconds.",
			"• 🍀 Server Luck (Robux or events) changes the 'Chance now' column for everyone.",
			"• ✨ Golden Hour / Golden boosts multiply mutation chances.",
			"• You always SEE the slime before buying it — nothing for sale is random.",
		}
		for i, text in notes do
			UIKit.label({ Text = text, Size = UDim2.new(1, -12, 0, 26), TextXAlignment = Enum.TextXAlignment.Left,
				TextColor3 = C.SubText, Font = Theme.FontBody, MaxTextSize = 17, StrokeThickness = 0, LayoutOrder = 40 + i,
				Parent = list })
		end
	end
	panel.onOpen = render
	for _, attr in { "LuckMult", "MutationMult" } do
		serverState:GetAttributeChangedSignal(attr):Connect(function()
			if UIKit.isOpen("Odds") then
				render()
			end
		end)
	end
end

function Progress.init()
	buildRebirth()
	buildOdds()
end

return Progress
