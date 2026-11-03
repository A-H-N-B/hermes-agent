-- Rewards: 7-day login streak, session playtime gifts and daily quests.
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Format = require(Shared:WaitForChild("Format"))
local RewardDefs = require(Shared:WaitForChild("RewardDefs"))
local Client = script.Parent.Parent
local Net = require(Client:WaitForChild("Net"))
local Notify = require(Client:WaitForChild("Notify"))
local State = require(Client:WaitForChild("State"))
local Theme = require(Client:WaitForChild("Theme"))
local UIKit = require(Client:WaitForChild("UIKit"))

local C = Theme.Colors
local New = UIKit.New
local Rewards = {}

local function call(action: string, payload: any?)
	local result = Net.call(action, payload)
	if not result.ok and result.msg then
		Notify.show(result.msg, "warning")
	end
end

function Rewards.init()
	local panel = UIKit.panel("Rewards", "🎁 REWARDS", Vector2.new(780, 500))
	local tab = "📅 Daily"
	local body = New("Frame", {
		Name = "Body",
		BackgroundTransparency = 1,
		Position = UDim2.fromOffset(0, 58),
		Size = UDim2.new(1, 0, 1, -58),
		Parent = panel.content,
	})
	local giftTimers = {}

	local function renderDaily()
		UIKit.clear(body)
		local daily = State.get("daily") or { streak = 0, canClaim = false, nextDay = 1, resetIn = 0 }
		UIKit.label({
			Text = "🔥 Streak: " .. daily.streak .. " day" .. (if daily.streak == 1 then "" else "s")
				.. "   ·   Come back every day for bigger rewards!",
			Size = UDim2.new(1, 0, 0, 30),
			MaxTextSize = 22,
			Parent = body,
		})
		local row = New("Frame", {
			BackgroundTransparency = 1,
			Position = UDim2.fromOffset(0, 40),
			Size = UDim2.new(1, 0, 0, 230),
			Parent = body,
		}, { UIKit.list(Enum.FillDirection.Horizontal, 8) })
		for day, reward in RewardDefs.Daily do
			local isNext = day == daily.nextDay
			local done = day < daily.nextDay or (isNext and not daily.canClaim)
			local frame = New("Frame", {
				BackgroundColor3 = if isNext and daily.canClaim then C.Pink elseif done then C.PanelLight else C.Card,
				Size = UDim2.fromOffset(98, 220),
				LayoutOrder = day,
				Parent = row,
			}, { UIKit.corner(0.12), UIKit.stroke(3, if isNext then C.Gold else C.Stroke) })
			UIKit.label({ Text = "Day " .. day, Size = UDim2.new(1, -8, 0, 30), Position = UDim2.fromOffset(4, 8),
				MaxTextSize = 24, Parent = frame })
			UIKit.label({
				Text = if day == 7 then "🏆" elseif reward.slimeMinRarity then "🟢" elseif reward.boostMinutes then "🧪" else "💰",
				Size = UDim2.new(1, 0, 0, 50),
				Position = UDim2.fromOffset(0, 42),
				StrokeThickness = 0,
				Parent = frame,
			})
			UIKit.label({
				Text = RewardDefs.describe(reward),
				Size = UDim2.new(1, -10, 0, 70),
				Position = UDim2.fromOffset(5, 96),
				TextWrapped = true,
				Font = Theme.FontBody,
				TextColor3 = C.SubText,
				MaxTextSize = 15,
				StrokeThickness = 0,
				Parent = frame,
			})
			if done then
				UIKit.label({ Text = "✓", Size = UDim2.new(1, 0, 0, 36), Position = UDim2.new(0, 0, 1, -44),
					TextColor3 = C.Green, Parent = frame })
			elseif isNext then
				UIKit.button({
					Text = "CLAIM",
					Color = C.Green,
					Size = UDim2.new(1, -12, 0, 40),
					Position = UDim2.new(0, 6, 1, -48),
					MaxTextSize = 20,
					Parent = frame,
					OnClick = function()
						call("claimDaily")
					end,
				})
			end
		end
		if not daily.canClaim then
			UIKit.label({
				Text = "Next reward in " .. Format.time(daily.resetIn),
				Size = UDim2.new(1, 0, 0, 30),
				Position = UDim2.fromOffset(0, 285),
				TextColor3 = C.SubText,
				MaxTextSize = 22,
				Parent = body,
			})
		end
	end

	local function renderGifts()
		UIKit.clear(body)
		table.clear(giftTimers)
		local gifts = State.get("gifts")
		if not gifts then
			return
		end
		local claimed = {}
		for _, i in gifts.claimed do
			claimed[i] = true
		end
		local grid = New("Frame", {
			BackgroundTransparency = 1,
			Size = UDim2.fromScale(1, 1),
			Parent = body,
		}, { UIKit.grid(Vector2.new(170, 180), 12) })
		for i, gift in RewardDefs.Gifts do
			local frame = New("Frame", {
				BackgroundColor3 = C.Card,
				LayoutOrder = i,
				Parent = grid,
			}, { UIKit.corner(0.12), UIKit.stroke(3) })
			UIKit.label({ Text = "🎁", Size = UDim2.new(1, 0, 0, 46), Position = UDim2.fromOffset(0, 6), StrokeThickness = 0,
				Parent = frame })
			UIKit.label({
				Text = RewardDefs.describe(gift.reward),
				Size = UDim2.new(1, -12, 0, 50),
				Position = UDim2.fromOffset(6, 54),
				TextWrapped = true,
				Font = Theme.FontBody,
				TextColor3 = C.SubText,
				MaxTextSize = 15,
				StrokeThickness = 0,
				Parent = frame,
			})
			if claimed[i] then
				UIKit.label({ Text = "✓ Claimed", Size = UDim2.new(1, 0, 0, 34), Position = UDim2.new(0, 0, 1, -44),
					TextColor3 = C.Green, MaxTextSize = 22, Parent = frame })
			else
				local button, label = UIKit.button({
					Text = "",
					Color = C.Gray,
					Size = UDim2.new(1, -16, 0, 40),
					Position = UDim2.new(0, 8, 1, -48),
					MaxTextSize = 20,
					Parent = frame,
					OnClick = function()
						call("claimGift", i)
					end,
				})
				table.insert(giftTimers, { button = button, label = label, at = gift.at * 60 })
			end
		end
	end

	local function renderQuests()
		UIKit.clear(body)
		UIKit.list(Enum.FillDirection.Vertical, 12).Parent = body
		UIKit.label({ Text = "New quests every day (UTC midnight)", Size = UDim2.new(1, 0, 0, 28), TextColor3 = C.SubText,
			MaxTextSize = 20, LayoutOrder = 0, Parent = body })
		for i, quest in State.get("quests") or {} do
			local row = New("Frame", {
				BackgroundColor3 = C.Card,
				Size = UDim2.new(1, 0, 0, 96),
				LayoutOrder = i,
				Parent = body,
			}, { UIKit.corner(0.15), UIKit.stroke(3) })
			UIKit.label({ Text = "📜 " .. quest.text, Size = UDim2.new(0.62, 0, 0, 34), Position = UDim2.fromOffset(14, 8),
				TextXAlignment = Enum.TextXAlignment.Left, MaxTextSize = 24, Parent = row })
			UIKit.label({ Text = "Reward: " .. RewardDefs.describe(quest.reward), Size = UDim2.new(0.62, 0, 0, 22),
				Position = UDim2.fromOffset(14, 42), TextXAlignment = Enum.TextXAlignment.Left, TextColor3 = C.SubText,
				Font = Theme.FontBody, MaxTextSize = 16, StrokeThickness = 0, Parent = row })
			local bar = UIKit.progress(row, UDim2.new(0.62, 0, 0, 20), C.Green)
			bar.frame.Position = UDim2.fromOffset(14, 68)
			local shown = if quest.kind == "collect" then Format.cash(quest.progress) .. " / " .. Format.cash(quest.target)
				else quest.progress .. " / " .. quest.target
			bar.set(quest.progress / math.max(quest.target, 1), shown)
			UIKit.button({
				Text = if quest.claimed then "✓ Done" elseif quest.done then "CLAIM" else "In progress",
				Color = if quest.claimed then C.Gray elseif quest.done then C.Green else C.PanelLight,
				Size = UDim2.new(0.3, -20, 0, 50),
				Position = UDim2.new(0.7, 0, 0.5, 0),
				AnchorPoint = Vector2.new(0, 0.5),
				MaxTextSize = 22,
				Parent = row,
				OnClick = if quest.done and not quest.claimed then function()
					call("claimQuest", i)
				end else nil,
			})
		end
	end

	local renderers = { ["📅 Daily"] = renderDaily, ["🎁 Gifts"] = renderGifts, ["📜 Quests"] = renderQuests }
	local select = UIKit.tabs(panel.content, { "📅 Daily", "🎁 Gifts", "📜 Quests" }, function(name)
		tab = name
		renderers[name]()
	end)
	panel.onOpen = function()
		renderers[tab]()
	end
	for key, name in { daily = "📅 Daily", gifts = "🎁 Gifts", quests = "📜 Quests" } do
		State.on(key, function()
			if UIKit.isOpen("Rewards") and tab == name then
				renderers[tab]()
			end
		end)
	end
	task.spawn(function()
		while true do
			task.wait(0.5)
			local gifts = State.get("gifts")
			if gifts and UIKit.isOpen("Rewards") and tab == "🎁 Gifts" then
				local elapsed = workspace:GetServerTimeNow() - gifts.startedAt
				for _, timer in giftTimers do
					local left = timer.at - elapsed
					if left <= 0 then
						timer.label.Text = "CLAIM!"
						timer.button.BackgroundColor3 = C.Green
					else
						timer.label.Text = "⏳ " .. Format.time(left)
					end
				end
			end
		end
	end)
	select("📅 Daily")
end

return Rewards
