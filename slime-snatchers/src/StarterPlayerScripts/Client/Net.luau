-- Client -> server requests through the single Action remote.
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Remotes = require(ReplicatedStorage:WaitForChild("Shared"):WaitForChild("Remotes"))

local Net = {}

local action = Remotes.get("Action")

function Net.call(name: string, payload: any?)
	local ok, result = pcall(function()
		return action:InvokeServer(name, payload)
	end)
	if not ok or type(result) ~= "table" then
		return { ok = false, msg = "Connection problem, try again!" }
	end
	return result
end

function Net.on(remoteName: string, fn: (...any) -> ())
	Remotes.get(remoteName).OnClientEvent:Connect(fn)
end

return Net
