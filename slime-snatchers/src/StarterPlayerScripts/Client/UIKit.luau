-- Small UI toolkit: styled labels/buttons, modal panels with one-at-a-time
-- behaviour, responsive scaling, tabs, progress bars and 2D slime icons.
local Lighting = game:GetService("Lighting")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local TweenService = game:GetService("TweenService")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Mutations = require(Shared:WaitForChild("Mutations"))
local New = require(Shared:WaitForChild("New"))
local Rarities = require(Shared:WaitForChild("Rarities"))
local Slimes = require(Shared:WaitForChild("Slimes"))
local Sounds = require(script.Parent:WaitForChild("Sounds"))
local Theme = require(script.Parent:WaitForChild("Theme"))

local C = Theme.Colors
local UIKit = {}
UIKit.New = New

local screen: ScreenGui? = nil
local panels = {}
local current: string? = nil
local scalers: { UIScale } = {}
local blur: BlurEffect? = nil

function UIKit.tween(inst: Instance, seconds: number, goal, style: Enum.EasingStyle?, direction: Enum.EasingDirection?)
	local t = TweenService:Create(inst, TweenInfo.new(seconds, style or Enum.EasingStyle.Quad,
		direction or Enum.EasingDirection.Out), goal)
	t:Play()
	return t
end

function UIKit.corner(radius: number?)
	return New("UICorner", { CornerRadius = UDim.new(radius or 0.18, 0) })
end

function UIKit.stroke(thickness: number?, color: Color3?, transparency: number?)
	return New("UIStroke", {
		Thickness = thickness or 3,
		Color = color or C.Stroke,
		Transparency = transparency or 0,
		ApplyStrokeMode = Enum.ApplyStrokeMode.Border,
	})
end

function UIKit.padding(px: number)
	local u = UDim.new(0, px)
	return New("UIPadding", { PaddingTop = u, PaddingBottom = u, PaddingLeft = u, PaddingRight = u })
end

function UIKit.list(direction: Enum.FillDirection?, padding: number?, hAlign: Enum.HorizontalAlignment?,
	vAlign: Enum.VerticalAlignment?)
	return New("UIListLayout", {
		FillDirection = direction or Enum.FillDirection.Vertical,
		Padding = UDim.new(0, padding or 8),
		SortOrder = Enum.SortOrder.LayoutOrder,
		HorizontalAlignment = hAlign or Enum.HorizontalAlignment.Center,
		VerticalAlignment = vAlign or Enum.VerticalAlignment.Top,
	})
end

function UIKit.grid(cell: Vector2, pad: number?)
	return New("UIGridLayout", {
		CellSize = UDim2.fromOffset(cell.X, cell.Y),
		CellPadding = UDim2.fromOffset(pad or 10, pad or 10),
		SortOrder = Enum.SortOrder.LayoutOrder,
		HorizontalAlignment = Enum.HorizontalAlignment.Center,
	})
end

function UIKit.gradient(top: Color3, bottom: Color3, rotation: number?)
	return New("UIGradient", { Color = ColorSequence.new(top, bottom), Rotation = rotation or 90 })
end

-- props are TextLabel properties plus MaxTextSize / StrokeThickness (0 = none).
function UIKit.label(props)
	local maxSize = props.MaxTextSize
	local strokeThickness = props.StrokeThickness
	props.MaxTextSize = nil
	props.StrokeThickness = nil
	if props.BackgroundTransparency == nil then
		props.BackgroundTransparency = 1
	end
	props.Font = props.Font or Theme.Font
	props.TextColor3 = props.TextColor3 or C.Text
	if props.TextScaled == nil then
		props.TextScaled = true
	end
	local label = New("TextLabel", props)
	if maxSize then
		New("UITextSizeConstraint", { MaxTextSize = maxSize, Parent = label })
	end
	if strokeThickness ~= 0 then
		New("UIStroke", {
			Thickness = strokeThickness or 2,
			Color = C.Stroke,
			ApplyStrokeMode = Enum.ApplyStrokeMode.Contextual,
			Parent = label,
		})
	end
	return label
end

-- props: Text, Color, Size, Position, AnchorPoint, LayoutOrder, Parent, OnClick,
--        TextColor, MaxTextSize, Name, Radius
function UIKit.button(props)
	local button = New("TextButton", {
		Name = props.Name or "Button",
		AutoButtonColor = false,
		BackgroundColor3 = props.Color or C.Green,
		Size = props.Size or UDim2.fromOffset(160, 50),
		Position = props.Position or UDim2.new(),
		AnchorPoint = props.AnchorPoint or Vector2.zero,
		LayoutOrder = props.LayoutOrder or 0,
		Text = "",
	})
	UIKit.corner(props.Radius or 0.28).Parent = button
	UIKit.stroke(3).Parent = button
	UIKit.gradient(Color3.new(1, 1, 1), Color3.fromRGB(190, 190, 200)).Parent = button
	local label = UIKit.label({
		Name = "Label",
		Text = props.Text or "",
		Size = UDim2.fromScale(0.92, 0.78),
		Position = UDim2.fromScale(0.5, 0.5),
		AnchorPoint = Vector2.new(0.5, 0.5),
		TextColor3 = props.TextColor or C.Text,
		MaxTextSize = props.MaxTextSize or 26,
		Parent = button,
	})
	local scale = New("UIScale", { Parent = button })
	button.MouseButton1Down:Connect(function()
		UIKit.tween(scale, 0.07, { Scale = 0.92 })
	end)
	local function release()
		UIKit.tween(scale, 0.12, { Scale = 1 }, Enum.EasingStyle.Back)
	end
	button.MouseButton1Up:Connect(release)
	button.MouseLeave:Connect(release)
	button.Activated:Connect(function()
		Sounds.play("Click")
		if props.OnClick then
			props.OnClick()
		end
	end)
	button.Parent = props.Parent
	return button, label
end

function UIKit.scroll(props)
	props.BackgroundTransparency = props.BackgroundTransparency or 1
	props.BorderSizePixel = 0
	props.ScrollBarThickness = props.ScrollBarThickness or 8
	props.ScrollBarImageColor3 = props.ScrollBarImageColor3 or C.SubText
	props.AutomaticCanvasSize = Enum.AutomaticSize.Y
	props.CanvasSize = UDim2.new()
	props.ScrollingDirection = Enum.ScrollingDirection.Y
	return New("ScrollingFrame", props)
end

function UIKit.progress(parent: Instance, size: UDim2, color: Color3?)
	local bar = New("Frame", {
		Name = "Progress",
		BackgroundColor3 = Color3.fromRGB(25, 20, 45),
		Size = size,
		Parent = parent,
	}, { UIKit.corner(0.5), UIKit.stroke(2) })
	local fill = New("Frame", {
		Name = "Fill",
		BackgroundColor3 = color or C.Green,
		Size = UDim2.fromScale(0, 1),
		Parent = bar,
	}, { UIKit.corner(0.5), UIKit.gradient(Color3.new(1, 1, 1), Color3.fromRGB(180, 180, 180)) })
	local text = UIKit.label({
		Size = UDim2.fromScale(1, 0.85),
		Position = UDim2.fromScale(0.5, 0.5),
		AnchorPoint = Vector2.new(0.5, 0.5),
		Text = "",
		MaxTextSize = 20,
		ZIndex = 3,
		Parent = bar,
	})
	return {
		frame = bar,
		set = function(fraction: number, caption: string?)
			fill.Size = UDim2.fromScale(math.clamp(fraction, 0, 1), 1)
			text.Text = caption or ""
		end,
	}
end

-- Tabs row; returns select(name). onSelect(name) is called on change.
function UIKit.tabs(parent: Instance, names: { string }, onSelect: (string) -> ())
	local row = New("Frame", {
		Name = "Tabs",
		BackgroundTransparency = 1,
		Size = UDim2.new(1, 0, 0, 46),
		Parent = parent,
	}, { UIKit.list(Enum.FillDirection.Horizontal, 8, Enum.HorizontalAlignment.Left) })
	local buttons = {}
	local function select(name: string)
		for n, b in buttons do
			b.BackgroundColor3 = if n == name then C.Pink else C.Card
		end
		onSelect(name)
	end
	for i, name in names do
		local button = UIKit.button({
			Text = name,
			Color = C.Card,
			Size = UDim2.new(1 / #names, -8, 1, 0),
			LayoutOrder = i,
			MaxTextSize = 22,
			Parent = row,
			OnClick = function()
				select(name)
			end,
		})
		buttons[name] = button
	end
	return select, row
end

-- 2D slime icon: a blob with eyes, coloured by slime/mutation.
function UIKit.slimeIcon(slimeId: string, mutKey: string?, size: UDim2, discovered: boolean?)
	local def = Slimes.ById[slimeId]
	local known = discovered ~= false
	local mut = Mutations.ByKey[mutKey or "None"]
	local color = if not known then Color3.fromRGB(70, 65, 90)
		elseif mut and mut.key ~= "None" and mut.key ~= "Rainbow" then mut.color
		else def.body
	local icon = New("Frame", {
		Name = "SlimeIcon",
		BackgroundColor3 = color,
		Size = size,
	}, {
		New("UICorner", { CornerRadius = UDim.new(0.5, 0) }),
		New("UIAspectRatioConstraint", { AspectRatio = 1.2 }),
		UIKit.stroke(2, Rarities.ByKey[def.rarity].color),
	})
	if known then
		if mut and mut.key == "Rainbow" then
			New("UIGradient", {
				Color = ColorSequence.new({
					ColorSequenceKeypoint.new(0, Color3.fromRGB(255, 90, 90)),
					ColorSequenceKeypoint.new(0.5, Color3.fromRGB(110, 255, 140)),
					ColorSequenceKeypoint.new(1, Color3.fromRGB(110, 140, 255)),
				}),
				Rotation = 30,
				Parent = icon,
			})
		end
		for _, x in { 0.3, 0.62 } do
			local eye = New("Frame", {
				BackgroundColor3 = Color3.new(1, 1, 1),
				Size = UDim2.fromScale(0.22, 0.27),
				Position = UDim2.fromScale(x, 0.3),
				Parent = icon,
			}, { New("UICorner", { CornerRadius = UDim.new(0.5, 0) }) })
			New("Frame", {
				BackgroundColor3 = Color3.fromRGB(25, 20, 30),
				Size = UDim2.fromScale(0.55, 0.55),
				Position = UDim2.fromScale(0.3, 0.32),
				Parent = eye,
			}, { New("UICorner", { CornerRadius = UDim.new(0.5, 0) }) })
		end
	else
		UIKit.label({ Text = "?", Size = UDim2.fromScale(0.6, 0.7), Position = UDim2.fromScale(0.2, 0.15), Parent = icon })
	end
	return icon
end

---------------------------------------------------------------------------
-- Responsive scale + panels
---------------------------------------------------------------------------
function UIKit.screenScale(): number
	local camera = workspace.CurrentCamera
	local viewport = if camera then camera.ViewportSize else Theme.Reference
	return math.clamp(math.min(viewport.X / Theme.Reference.X, viewport.Y / Theme.Reference.Y), 0.5, 1.3)
end

-- Gives a top-level container its own UIScale that follows the screen size.
function UIKit.scaled(frame: GuiObject)
	local scale = New("UIScale", { Name = "ScreenScale", Scale = UIKit.screenScale(), Parent = frame })
	table.insert(scalers, scale)
	return scale
end

function UIKit.init(gui: ScreenGui)
	screen = gui
	local function refresh()
		local s = UIKit.screenScale()
		for _, scale in scalers do
			scale.Scale = s
		end
	end
	local function hookCamera()
		local camera = workspace.CurrentCamera
		if camera then
			camera:GetPropertyChangedSignal("ViewportSize"):Connect(refresh)
		end
		refresh()
	end
	workspace:GetPropertyChangedSignal("CurrentCamera"):Connect(hookCamera)
	hookCamera()
	blur = New("BlurEffect", { Name = "PanelBlur", Size = 0, Parent = Lighting })
end

function UIKit.panel(name: string, title: string, size: Vector2)
	assert(screen, "UIKit.init first")
	local holder = New("Frame", {
		Name = name,
		AnchorPoint = Vector2.new(0.5, 0.5),
		Position = UDim2.fromScale(0.5, 0.52),
		Size = UDim2.fromOffset(size.X, size.Y),
		BackgroundTransparency = 1,
		Visible = false,
		ZIndex = 20,
		Parent = screen,
	})
	UIKit.scaled(holder)
	local frame = New("Frame", {
		Name = "Body",
		Size = UDim2.fromScale(1, 1),
		BackgroundColor3 = C.Panel,
		Parent = holder,
	}, {
		UIKit.corner(0.05),
		UIKit.stroke(5),
		UIKit.gradient(Color3.fromRGB(255, 255, 255), Color3.fromRGB(200, 190, 230)),
	})
	local pop = New("UIScale", { Name = "Pop", Parent = frame })
	local titleLabel = UIKit.label({
		Name = "Title",
		Text = title,
		Size = UDim2.new(1, -140, 0, 50),
		Position = UDim2.fromOffset(70, 10),
		MaxTextSize = 40,
		StrokeThickness = 3,
		Parent = frame,
	})
	local content = New("Frame", {
		Name = "Content",
		BackgroundTransparency = 1,
		Position = UDim2.fromOffset(16, 68),
		Size = UDim2.new(1, -32, 1, -84),
		Parent = frame,
	})
	local panel = {
		name = name,
		holder = holder,
		frame = frame,
		content = content,
		title = titleLabel,
		pop = pop,
		onOpen = nil :: (() -> ())?,
	}
	UIKit.button({
		Name = "Close",
		Text = "✕",
		Color = C.Red,
		Size = UDim2.fromOffset(52, 52),
		Position = UDim2.new(1, -10, 0, -10),
		AnchorPoint = Vector2.new(0.5, 0.5),
		Radius = 0.5,
		MaxTextSize = 30,
		Parent = frame,
		OnClick = function()
			UIKit.close(name)
		end,
	})
	panels[name] = panel
	return panel
end

function UIKit.open(name: string)
	local panel = panels[name]
	if not panel then
		return
	end
	if current and current ~= name then
		UIKit.close(current)
	end
	current = name
	panel.holder.Visible = true
	panel.pop.Scale = 0.75
	UIKit.tween(panel.pop, 0.25, { Scale = 1 }, Enum.EasingStyle.Back)
	if blur then
		UIKit.tween(blur, 0.2, { Size = 10 })
	end
	if panel.onOpen then
		task.spawn(panel.onOpen)
	end
end

function UIKit.close(name: string)
	local panel = panels[name]
	if not panel then
		return
	end
	panel.holder.Visible = false
	if current == name then
		current = nil
		if blur then
			UIKit.tween(blur, 0.2, { Size = 0 })
		end
	end
end

function UIKit.toggle(name: string)
	if current == name then
		UIKit.close(name)
	else
		UIKit.open(name)
	end
end

function UIKit.isOpen(name: string): boolean
	return current == name
end

function UIKit.clear(container: Instance)
	for _, child in container:GetChildren() do
		if child:IsA("GuiObject") then
			child:Destroy()
		end
	end
end

return UIKit
