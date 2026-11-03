-- Server bootstrap: loads every service, wires them together (dependency
-- injection through the shared `S` table avoids require cycles), then starts them.
-- ORDER matters: PlayerService runs each service's onReady hook in this order
-- (passes are checked before the plot is sized, the plot exists before slimes spawn).
local Services = script.Parent:WaitForChild("Services")

local ORDER = {
	"Analytics",
	"DataService",
	"WorldBuilder",
	"PlayerService",
	"EventService",
	"MonetizationService",
	"PlotService",
	"EconomyService",
	"SlimeService",
	"CarryService",
	"RewardService",
	"LeaderboardService",
	"AdminService",
	"ToolService",
}

local S = {}
for _, name in ORDER do
	S[name] = require(Services:WaitForChild(name))
end
for _, name in ORDER do
	local service = S[name]
	if service.init then
		service.init(S)
	end
end
for _, name in ORDER do
	local service = S[name]
	if service.start then
		task.spawn(service.start)
	end
end

print(string.format("[SlimeSnatchers] server ready (%d services)", #ORDER))
