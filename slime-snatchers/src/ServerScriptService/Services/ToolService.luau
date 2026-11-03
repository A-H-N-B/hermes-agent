-- The Bonk Hammer: everyone gets one. Bonking a thief makes them drop the
-- stolen slime (it flies home) and stuns them; other players just get knocked back.
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local TweenService = game:GetService("TweenService")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Config = require(Shared:WaitForChild("Config"))
local New = require(Shared:WaitForChild("New"))

local Tuning = Config.Tuning
local ToolService = {}

local S
local template: Tool
local lastSwing: { [Player]: number } = {}

local TOOL_NAME = "Bonk Hammer"
local REST_GRIP = CFrame.new(0, -1.1, 0)
local SWING_GRIP = CFrame.new(0, -1.1, 0) * CFrame.Angles(math.rad(-80), 0, 0)

local function buildTemplate(): Tool
	local tool = New("Tool", {
		Name = TOOL_NAME,
		RequiresHandle = true,
		CanBeDropped = false,
		ToolTip = "Bonk thieves to get slimes back!",
		Grip = REST_GRIP,
	})
	local handle = New("Part", {
		Name = "Handle",
		Size = Vector3.new(0.5, 3.4, 0.5),
		Color = Color3.fromRGB(150, 100, 60),
		Material = Enum.Material.Wood,
		CanCollide = false,
		Massless = true,
		Parent = tool,
	})
	local head = New("Part", {
		Name = "Head",
		Size = Vector3.new(1.8, 1.8, 2.8),
		Color = Color3.fromRGB(255, 90, 140),
		Material = Enum.Material.SmoothPlastic,
		CanCollide = false,
		Massless = true,
		CFrame = handle.CFrame * CFrame.new(0, 1.8, 0),
		Parent = tool,
	})
	New("WeldConstraint", { Part0 = handle, Part1 = head, Parent = head })
	New("Sparkles", { SparkleColor = Color3.fromRGB(255, 180, 220), Enabled = false, Parent = head })
	return tool
end

local function rootOf(player: Player): BasePart?
	local character = player.Character
	local root = character and character:FindFirstChild("HumanoidRootPart")
	return if root and root:IsA("BasePart") then root else nil
end

local function swing(player: Player, tool: Tool)
	local now = os.clock()
	if (lastSwing[player] or 0) + Tuning.BonkCooldown > now then
		return
	end
	lastSwing[player] = now
	local root = rootOf(player)
	if not root then
		return
	end
	local info = TweenInfo.new(0.1, Enum.EasingStyle.Quad, Enum.EasingDirection.Out)
	local down = TweenService:Create(tool, info, { Grip = SWING_GRIP })
	down.Completed:Connect(function()
		TweenService:Create(tool, TweenInfo.new(0.18), { Grip = REST_GRIP }):Play()
	end)
	down:Play()

	local look = root.CFrame.LookVector
	for _, other in S.PlayerService.readyPlayers() do
		local otherRoot = other ~= player and rootOf(other)
		if otherRoot then
			local delta = otherRoot.Position - root.Position
			local distance = delta.Magnitude
			if distance <= Tuning.BonkRange and distance > 0 and look:Dot(delta.Unit) > 0.2 then
				local direction = (delta.Unit + Vector3.new(0, 0.6, 0)).Unit
				S.PlayerService.fxAll("bonk", { position = otherRoot.Position })
				if S.CarryService.isCarrying(other) then
					S.CarryService.drop(other, "bonked by " .. player.DisplayName)
					S.PlayerService.stun(other, Tuning.BonkStunSeconds)
					S.PlayerService.fx(other, "knockback", { direction = direction, power = 75 })
					S.PlayerService.notify(player, "💥 BONK! You stopped " .. other.DisplayName .. "!", "success")
					S.RewardService.progress(player, "bonk", 1)
					local profile = S.DataService.get(player)
					if profile then
						profile.data.stats.bonks += 1
					end
				else
					S.PlayerService.fx(other, "knockback", { direction = direction, power = 40 })
				end
			end
		end
	end
end

function ToolService.equipStarterTools(player: Player)
	local backpack = player:FindFirstChildOfClass("Backpack")
	local character = player.Character
	if not backpack or backpack:FindFirstChild(TOOL_NAME) or (character and character:FindFirstChild(TOOL_NAME)) then
		return
	end
	local tool = template:Clone()
	tool.Activated:Connect(function()
		swing(player, tool)
	end)
	tool.Parent = backpack
end

function ToolService.init(services)
	S = services
	template = buildTemplate()
	S.PlayerService.onLeaving(function(player)
		lastSwing[player] = nil
	end)
end

return ToolService
