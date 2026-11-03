-- Toast notifications (top centre), colour-coded by kind.
local Sounds = require(script.Parent:WaitForChild("Sounds"))
local Theme = require(script.Parent:WaitForChild("Theme"))
local UIKit = require(script.Parent:WaitForChild("UIKit"))

local C = Theme.Colors
local New = UIKit.New
local Notify = {}

local container: Frame? = nil
local count = 0
local MAX_VISIBLE = 4

local KINDS = {
	info = { color = C.Blue, seconds = 3.5 },
	success = { color = C.Green, seconds = 3.5 },
	error = { color = C.Red, seconds = 3 },
	warning = { color = C.Gold, seconds = 3.5 },
	event = { color = C.Purple, seconds = 5 },
	purchase = { color = C.Pink, seconds = 5 },
	rare = { color = C.Gold, seconds = 4.5 },
	alert = { color = C.Red, seconds = 5, sound = "Alert" },
}

function Notify.init(gui: ScreenGui)
	container = New("Frame", {
		Name = "Toasts",
		AnchorPoint = Vector2.new(0.5, 0),
		Position = UDim2.new(0.5, 0, 0, 104),
		Size = UDim2.fromOffset(560, 300),
		BackgroundTransparency = 1,
		ZIndex = 30,
		Parent = gui,
	}, { UIKit.list(Enum.FillDirection.Vertical, 6) })
	UIKit.scaled(container)
end

function Notify.show(text: string, kind: string?)
	if not container or type(text) ~= "string" then
		return
	end
	local style = KINDS[kind or "info"] or KINDS.info
	count += 1
	local children = container:GetChildren()
	local toasts = {}
	for _, child in children do
		if child:IsA("Frame") then
			table.insert(toasts, child)
		end
	end
	if #toasts >= MAX_VISIBLE then
		table.sort(toasts, function(a, b)
			return a.LayoutOrder < b.LayoutOrder
		end)
		toasts[1]:Destroy()
	end
	local toast = New("Frame", {
		Name = "Toast",
		BackgroundColor3 = C.Panel,
		BackgroundTransparency = 0.05,
		Size = UDim2.fromOffset(540, 46),
		LayoutOrder = count,
		Parent = container,
	}, {
		UIKit.corner(0.3),
		UIKit.stroke(3, style.color),
	})
	local label = UIKit.label({
		Text = text,
		Size = UDim2.new(1, -24, 0.8, 0),
		Position = UDim2.fromScale(0.5, 0.5),
		AnchorPoint = Vector2.new(0.5, 0.5),
		MaxTextSize = 22,
		Parent = toast,
	})
	local scale = New("UIScale", { Scale = 0.6, Parent = toast })
	UIKit.tween(scale, 0.25, { Scale = 1 }, Enum.EasingStyle.Back)
	if style.sound then
		Sounds.play(style.sound)
	end
	task.delay(style.seconds, function()
		if toast.Parent then
			UIKit.tween(toast, 0.3, { BackgroundTransparency = 1 })
			UIKit.tween(label, 0.3, { TextTransparency = 1 })
			task.wait(0.3)
			toast:Destroy()
		end
	end)
end

return Notify
