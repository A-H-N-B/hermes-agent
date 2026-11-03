-- First-time guide: a hint bar plus a glowing guide line from the player to
-- the next thing to do (buy -> collect -> lock -> steal). The server advances
-- the step when the player actually does it.
local Players = game:GetService("Players")

local Net = require(script.Parent:WaitForChild("Net"))
local State = require(script.Parent:WaitForChild("State"))
local Theme = require(script.Parent:WaitForChild("Theme"))
local UIKit = require(script.Parent:WaitForChild("UIKit"))

local C = Theme.Colors
local New = UIKit.New
local player = Players.LocalPlayer
local Tutorial = {}

local STEPS = {
	[0] = { text = "👉 Walk to the conveyor and BUY a slime (press E or tap)!", target = "belt" },
	[1] = { text = "💰 Step on the GREEN plate next to your slime to collect cash!", target = "collect" },
	[2] = { text = "🔒 Step on the RED button at your base entrance to lock it!", target = "lock" },
	[3] = { text = "🔒 Step on the RED button at your base entrance to lock it!", target = "lock" },
	[4] = { text = "😈 Sneak into other bases and HOLD E on a slime to steal it. 🔨 Bonk thieves!", autoFinish = 25 },
}
local DONE = 5

local function myPlot(): Instance?
	local plots = workspace:FindFirstChild("Plots")
	if not plots then
		return nil
	end
	for _, plot in plots:GetChildren() do
		if plot:GetAttribute("OwnerId") == player.UserId then
			return plot
		end
	end
	return nil
end

local function findTarget(kind: string?, from: Vector3): BasePart?
	if kind == "belt" then
		local folder = workspace:FindFirstChild("BeltSlimes")
		local best, bestDist = nil, math.huge
		for _, model in if folder then folder:GetChildren() else {} do
			local body = model:IsA("Model") and model.PrimaryPart
			if body then
				local d = (body.Position - from).Magnitude
				if d < bestDist then
					best, bestDist = body, d
				end
			end
		end
		return best
	end
	local plot = myPlot()
	if not plot then
		return nil
	end
	if kind == "lock" then
		local button = plot:FindFirstChild("LockButton")
		return if button and button:IsA("BasePart") then button else nil
	end
	if kind == "collect" then
		local pads = plot:FindFirstChild("Pads")
		for i = 1, 40 do
			local pad = pads and pads:FindFirstChild("Pad" .. i)
			local collect = pad and pad:FindFirstChild("Collect")
			if collect and collect:IsA("BasePart") and collect.Transparency < 1 then
				return collect
			end
		end
	end
	return nil
end

function Tutorial.init(gui: ScreenGui)
	local hint = New("Frame", {
		Name = "Hint",
		AnchorPoint = Vector2.new(0.5, 1),
		-- above the bottom HUD group at every UI scale
		Position = UDim2.new(0.5, 0, 0.72, 0),
		Size = UDim2.fromOffset(640, 52),
		BackgroundColor3 = C.Panel,
		Visible = false,
		Parent = gui,
	}, { UIKit.corner(0.4), UIKit.stroke(4, C.Gold) })
	UIKit.scaled(hint)
	local label = UIKit.label({ Text = "", Size = UDim2.new(1, -24, 0.8, 0), Position = UDim2.fromScale(0.5, 0.5),
		AnchorPoint = Vector2.new(0.5, 0.5), MaxTextSize = 24, Parent = hint })

	local anchor = New("Part", {
		Name = "TutorialTarget",
		Anchored = true,
		CanCollide = false,
		CanQuery = false,
		CanTouch = false,
		Transparency = 1,
		Size = Vector3.new(0.2, 0.2, 0.2),
		Parent = workspace,
	})
	local targetAttachment = New("Attachment", { Parent = anchor })
	local beam = New("Beam", {
		Attachment1 = targetAttachment,
		Color = ColorSequence.new(Color3.fromRGB(255, 230, 90)),
		Width0 = 0.6,
		Width1 = 0.6,
		FaceCamera = true,
		LightEmission = 1,
		Segments = 12,
		CurveSize0 = 4,
		Enabled = false,
		Parent = anchor,
	})
	local autoStarted = false

	task.spawn(function()
		while true do
			task.wait(0.4)
			local step = State.get("tutorial") or 0
			local def = STEPS[step]
			local character = player.Character
			local root = character and character:FindFirstChild("HumanoidRootPart")
			if step >= DONE or not def or not root or not root:IsA("BasePart") then
				hint.Visible = false
				beam.Enabled = false
				continue
			end
			hint.Visible = true
			label.Text = def.text
			local target = findTarget(def.target, root.Position)
			if target then
				local attach = root:FindFirstChild("TutorialSource")
				if not attach then
					attach = New("Attachment", { Name = "TutorialSource", Parent = root })
				end
				beam.Attachment0 = attach :: Attachment
				anchor.Position = target.Position + Vector3.new(0, 1, 0)
				beam.Enabled = true
			else
				beam.Enabled = false
			end
			if def.autoFinish and not autoStarted then
				autoStarted = true
				task.delay(def.autoFinish, function()
					Net.call("tutorial", DONE)
					State.apply({ tutorial = DONE })
				end)
			end
		end
	end)
end

return Tutorial
