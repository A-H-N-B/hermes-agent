-- Remote definitions. The server creates them on boot; clients wait for them.
--   Action (RemoteFunction, client -> server): every player request goes through
--          one validated, rate-limited entry point.
--   Sync   (RemoteEvent, server -> client): partial state updates for that player.
--   Notify (RemoteEvent, server -> client): toast messages.
--   FX     (RemoteEvent, server -> client): visual/sound effects.
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService = game:GetService("RunService")

local Remotes = {}

local DEFINITIONS = {
	Action = "RemoteFunction",
	Sync = "RemoteEvent",
	Notify = "RemoteEvent",
	FX = "RemoteEvent",
}

local folder: Folder? = nil

local function getFolder(): Folder
	if folder then
		return folder
	end
	if RunService:IsServer() then
		local existing = ReplicatedStorage:FindFirstChild("Remotes")
		if not existing then
			existing = Instance.new("Folder")
			existing.Name = "Remotes"
			existing.Parent = ReplicatedStorage
		end
		folder = existing :: Folder
	else
		folder = ReplicatedStorage:WaitForChild("Remotes") :: Folder
	end
	return folder :: Folder
end

function Remotes.init()
	local root = getFolder()
	for name, className in DEFINITIONS do
		if not root:FindFirstChild(name) then
			local remote = Instance.new(className)
			remote.Name = name
			remote.Parent = root
		end
	end
end

function Remotes.get(name: string): any
	assert(DEFINITIONS[name], "unknown remote " .. name)
	local root = getFolder()
	if RunService:IsServer() then
		local remote = root:FindFirstChild(name)
		if not remote then
			Remotes.init()
			remote = root:FindFirstChild(name)
		end
		return remote
	end
	return root:WaitForChild(name)
end

return Remotes
