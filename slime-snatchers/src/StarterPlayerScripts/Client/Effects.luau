-- Client-side juice: floating cash text, confetti, particle bursts, screen
-- flashes, knockback, idle bobbing and rainbow colour cycling of slimes.
local Debris = game:GetService("Debris")
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService = game:GetService("RunService")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Format = require(Shared:WaitForChild("Format"))
local Rarities = require(Shared:WaitForChild("Rarities"))
local Notify = require(script.Parent:WaitForChild("Notify"))
local Sounds = require(script.Parent:WaitForChild("Sounds"))
local State = require(script.Parent:WaitForChild("State"))
local Theme = require(script.Parent:WaitForChild("Theme"))
local UIKit = require(script.Parent:WaitForChild("UIKit"))

local New = UIKit.New
local C = Theme.Colors
local player = Players.LocalPlayer
local Effects = {}

local overlay: Frame? = nil
local bobbing: { [Model]: number } = {}
local rainbow: { [Model]: boolean } = {}
local lastTip = 0

local function lowGfx(): boolean
	local settings = State.get("settings")
	return settings ~= nil and settings.lowGfx == true
end

local function nearCamera(position: Vector3, range: number): boolean
	local camera = workspace.CurrentCamera
	return camera ~= nil and (camera.CFrame.Position - position).Magnitude <= range
end

local function floatingText(position: Vector3, text: string, color: Color3)
	local anchor = New("Part", {
		Anchored = true,
		CanCollide = false,
		CanQuery = false,
		CanTouch = false,
		Transparency = 1,
		Size = Vector3.new(0.2, 0.2, 0.2),
		CFrame = CFrame.new(position),
		Parent = workspace,
	})
	local gui = New("BillboardGui", {
		Size = UDim2.fromScale(6, 1.6),
		StudsOffsetWorldSpace = Vector3.new(0, 2, 0),
		AlwaysOnTop = true,
		LightInfluence = 0,
		Adornee = anchor,
		Parent = anchor,
	})
	local label = UIKit.label({ Text = text, Size = UDim2.fromScale(1, 1), TextColor3 = color, StrokeThickness = 3, Parent = gui })
	UIKit.tween(gui, 1.1, { StudsOffsetWorldSpace = Vector3.new(0, 6, 0) })
	UIKit.tween(label, 1.1, { TextTransparency = 1 }, Enum.EasingStyle.Quad, Enum.EasingDirection.In)
	Debris:AddItem(anchor, 1.2)
end

local function burst(position: Vector3, color: Color3, count: number)
	if lowGfx() or not nearCamera(position, 160) then
		return
	end
	local anchor = New("Part", {
		Anchored = true,
		CanCollide = false,
		CanQuery = false,
		CanTouch = false,
		Transparency = 1,
		Size = Vector3.new(0.2, 0.2, 0.2),
		CFrame = CFrame.new(position),
		Parent = workspace,
	})
	local emitter = New("ParticleEmitter", {
		Rate = 0,
		Lifetime = NumberRange.new(0.5, 1),
		Speed = NumberRange.new(8, 18),
		SpreadAngle = Vector2.new(180, 180),
		Size = NumberSequence.new({ NumberSequenceKeypoint.new(0, 0.6), NumberSequenceKeypoint.new(1, 0) }),
		Color = ColorSequence.new(color),
		LightEmission = 0.8,
		Acceleration = Vector3.new(0, -25, 0),
		Parent = anchor,
	})
	emitter:Emit(count)
	Debris:AddItem(anchor, 1.5)
end

local function flash(color: Color3, times: number)
	if not overlay then
		return
	end
	task.spawn(function()
		for _ = 1, times do
			overlay.BackgroundColor3 = color
			overlay.BackgroundTransparency = 0.55
			UIKit.tween(overlay, 0.35, { BackgroundTransparency = 1 })
			task.wait(0.4)
		end
	end)
end

local function bigText(text: string, color: Color3)
	if not overlay then
		return
	end
	local label = UIKit.label({
		Text = text,
		Size = UDim2.fromScale(0.7, 0.14),
		Position = UDim2.fromScale(0.5, 0.36),
		AnchorPoint = Vector2.new(0.5, 0.5),
		TextColor3 = color,
		StrokeThickness = 4,
		ZIndex = 40,
		Parent = overlay,
	})
	local scale = New("UIScale", { Scale = 0.3, Parent = label })
	UIKit.tween(scale, 0.35, { Scale = 1 }, Enum.EasingStyle.Back)
	task.delay(1.6, function()
		UIKit.tween(label, 0.4, { TextTransparency = 1 })
		task.wait(0.45)
		label:Destroy()
	end)
end

local function confetti()
	if not overlay or lowGfx() then
		return
	end
	local colors = { C.Pink, C.Gold, C.Green, C.Blue, C.Purple }
	for _ = 1, 45 do
		local piece = New("Frame", {
			BackgroundColor3 = colors[math.random(1, #colors)],
			Size = UDim2.fromOffset(math.random(8, 14), math.random(12, 20)),
			Position = UDim2.fromScale(math.random(), -0.05),
			Rotation = math.random(0, 360),
			ZIndex = 39,
			Parent = overlay,
		})
		local duration = 1.6 + math.random() * 1.2
		UIKit.tween(piece, duration, {
			Position = UDim2.fromScale(math.random(), 1.05),
			Rotation = piece.Rotation + math.random(-360, 360),
		}, Enum.EasingStyle.Quad, Enum.EasingDirection.In)
		Debris:AddItem(piece, duration + 0.1)
	end
end

local function shake(seconds: number, strength: number)
	local camera = workspace.CurrentCamera
	if not camera or lowGfx() then
		return
	end
	local stop = os.clock() + seconds
	local connection
	connection = RunService.RenderStepped:Connect(function()
		if os.clock() > stop then
			connection:Disconnect()
			return
		end
		local s = strength * (stop - os.clock()) / seconds
		camera.CFrame *= CFrame.new(math.random(-100, 100) / 100 * s, math.random(-100, 100) / 100 * s, 0)
	end)
end

local function tip(text: string)
	if os.clock() - lastTip > 120 then
		lastTip = os.clock()
		Notify.show(text, "info")
	end
end

local HANDLERS = {}

function HANDLERS.cash(data)
	floatingText(data.position, "+" .. Format.cash(data.amount), C.Cash)
	Sounds.play("Cash")
end

function HANDLERS.celebrate(data)
	confetti()
	bigText(tostring(data.text or "WOW!"), C.Gold)
	Sounds.play("Buy")
end

function HANDLERS.event(data)
	bigText(tostring(data.name), C.Purple)
	flash(C.Purple, 1)
	Sounds.play("Alert")
end

function HANDLERS.rareSpawn(data)
	local rarity = Rarities.ByKey[data.rarity]
	flash(if rarity then rarity.color else C.Gold, 1)
	Sounds.play("LevelUp")
end

function HANDLERS.poof(data)
	burst(data.position, Color3.fromRGB(140, 255, 170), 14)
end

function HANDLERS.levelup(data)
	burst(data.position, C.Gold, 18)
	floatingText(data.position + Vector3.new(0, 2, 0), "LEVEL " .. tostring(data.level) .. "!", C.Gold)
	Sounds.play("LevelUp")
end

function HANDLERS.fuse(data)
	burst(data.position, C.Purple, 40)
	confetti()
	Sounds.play("LevelUp")
end

function HANDLERS.bonk(data)
	burst(data.position, C.Pink, 20)
	if nearCamera(data.position, 80) then
		Sounds.play("Bonk")
	end
end

function HANDLERS.knockback(data)
	local character = player.Character
	local root = character and character:FindFirstChild("HumanoidRootPart")
	local humanoid = character and character:FindFirstChildOfClass("Humanoid")
	if root and root:IsA("BasePart") and humanoid and typeof(data.direction) == "Vector3" then
		humanoid:ChangeState(Enum.HumanoidStateType.FallingDown)
		root.AssemblyLinearVelocity = data.direction * (tonumber(data.power) or 50)
		shake(0.3, 0.6)
	end
end

function HANDLERS.stolenAlert(_data)
	flash(C.Red, 3)
	shake(0.5, 0.8)
end

function HANDLERS.stolen(_data)
	bigText("😭 STOLEN!", C.Red)
	flash(C.Red, 1)
end

function HANDLERS.sound(data)
	Sounds.play(tostring(data.name))
end

function HANDLERS.noCash(_data)
	tip("💡 Tip: collect cash from the green plates, or grab a Cash pack in the 🛒 Shop!")
end

function HANDLERS.baseFull(_data)
	tip("💡 Tip: fuse 3 identical slimes 🟢 or ⭐ Rebirth for more pads!")
end

function Effects.play(name: string, data)
	local handler = HANDLERS[name]
	if handler then
		local ok, err = pcall(handler, data or {})
		if not ok then
			warn("[Effects] " .. name .. ": " .. tostring(err))
		end
	end
end

local function track(model: Instance)
	if not model:IsA("Model") then
		return
	end
	if model:GetAttribute("BaseCFrame") ~= nil then
		bobbing[model] = math.random() * math.pi * 2
	end
	if model:GetAttribute("Rainbow") then
		rainbow[model] = true
	end
end

function Effects.init(gui: ScreenGui)
	overlay = New("Frame", {
		Name = "FX",
		BackgroundTransparency = 1,
		BackgroundColor3 = C.Red,
		Size = UDim2.fromScale(1, 1),
		ZIndex = 38,
		Parent = gui,
	})
	for _, folderName in { "BaseSlimes", "BeltSlimes" } do
		task.spawn(function()
			local folder = workspace:WaitForChild(folderName)
			for _, child in folder:GetChildren() do
				track(child)
			end
			folder.ChildAdded:Connect(function(child)
				-- attributes are set before parenting, so they're ready here
				track(child)
			end)
			folder.ChildRemoved:Connect(function(child)
				bobbing[child :: Model] = nil
				rainbow[child :: Model] = nil
			end)
		end)
	end
	RunService.RenderStepped:Connect(function()
		local t = os.clock()
		local camera = workspace.CurrentCamera
		if not camera then
			return
		end
		local camPos = camera.CFrame.Position
		local low = lowGfx()
		for model, phase in bobbing do
			local body = model.PrimaryPart
			local base = model:GetAttribute("BaseCFrame")
			if body and typeof(base) == "CFrame" and not low and (base.Position - camPos).Magnitude < 130 then
				body.CFrame = base
					* CFrame.new(0, math.abs(math.sin(t * 2.4 + phase)) * 0.35, 0)
					* CFrame.Angles(0, math.sin(t * 1.1 + phase) * 0.2, 0)
			end
		end
		local hue = (t * 0.25) % 1
		for model in rainbow do
			local body = model.PrimaryPart
			if body then
				body.Color = Color3.fromHSV(hue, 0.65, 1)
			end
		end
	end)
end

return Effects
