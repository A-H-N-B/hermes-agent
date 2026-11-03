-- Local world tweaks: only show prompts that make sense for this player
-- (no "Steal" on your own slimes, no "Sell" on others'), let the owner walk
-- through their own lock barrier, and open UI panels from world prompts.
local Players = game:GetService("Players")
local ProximityPromptService = game:GetService("ProximityPromptService")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Config = require(ReplicatedStorage:WaitForChild("Shared"):WaitForChild("Config"))
local State = require(script.Parent:WaitForChild("State"))

local player = Players.LocalPlayer
local World = {}

local function filterPrompt(prompt: Instance)
	if not prompt:IsA("ProximityPrompt") then
		return
	end
	local mode = prompt:GetAttribute("Mode")
	local ownerId = prompt:GetAttribute("OwnerId")
	if mode == "steal" then
		prompt.Enabled = ownerId ~= player.UserId
		if State.has("FastSteal") then
			prompt.HoldDuration = Config.Tuning.StealHoldSeconds / 2
		end
	elseif mode == "sell" then
		prompt.Enabled = ownerId == player.UserId
	end
end

local function watchFolder(name: string)
	task.spawn(function()
		local folder = workspace:WaitForChild(name)
		for _, d in folder:GetDescendants() do
			filterPrompt(d)
		end
		folder.DescendantAdded:Connect(filterPrompt)
	end)
end

local function refreshAllPrompts()
	local folder = workspace:FindFirstChild("BaseSlimes")
	if folder then
		for _, d in folder:GetDescendants() do
			filterPrompt(d)
		end
	end
end

local function hookBarrier(plot: Instance)
	local barrier = plot:FindFirstChild("Barrier")
	if not barrier or not barrier:IsA("BasePart") then
		return
	end
	local function apply()
		if plot:GetAttribute("OwnerId") == player.UserId and barrier.CanCollide then
			barrier.CanCollide = false
		end
	end
	barrier:GetPropertyChangedSignal("CanCollide"):Connect(apply)
	plot:GetAttributeChangedSignal("OwnerId"):Connect(apply)
	apply()
end

function World.init(openPanel: (string) -> ())
	watchFolder("BaseSlimes")
	task.spawn(function()
		local plots = workspace:WaitForChild("Plots")
		for _, plot in plots:GetChildren() do
			hookBarrier(plot)
		end
		plots.ChildAdded:Connect(hookBarrier)
	end)
	State.on("owned", refreshAllPrompts)
	State.on("gifted", refreshAllPrompts)
	ProximityPromptService.PromptTriggered:Connect(function(prompt)
		local panel = prompt:GetAttribute("OpenPanel")
		if type(panel) == "string" then
			openPanel(panel)
		end
	end)
end

return World
