-- Free reward tables shared by the server (grants) and client (UI previews).
-- reward: { cashMinutes?, minCash?, boostMinutes?, slimeMinRarity? }
local RewardDefs = {}

RewardDefs.Daily = {
	{ cashMinutes = 5, minCash = 500 },
	{ cashMinutes = 10, minCash = 1500 },
	{ boostMinutes = 15 },
	{ cashMinutes = 20, minCash = 5000 },
	{ slimeMinRarity = "Rare" },
	{ cashMinutes = 45, minCash = 20000 },
	{ slimeMinRarity = "Epic", boostMinutes = 30 },
}

-- Session playtime gifts; `at` is minutes played in the current session.
RewardDefs.Gifts = {
	{ at = 2, reward = { cashMinutes = 3, minCash = 300 } },
	{ at = 5, reward = { boostMinutes = 5 } },
	{ at = 10, reward = { cashMinutes = 8, minCash = 1500 } },
	{ at = 15, reward = { slimeMinRarity = "Uncommon" } },
	{ at = 25, reward = { cashMinutes = 15, minCash = 6000 } },
	{ at = 35, reward = { boostMinutes = 15 } },
	{ at = 50, reward = { slimeMinRarity = "Rare" } },
	{ at = 70, reward = { cashMinutes = 30, minCash = 25000 } },
}

-- Short human-readable description used by the UI.
function RewardDefs.describe(reward): string
	local parts = {}
	if reward.cashMinutes then
		table.insert(parts, "💰 " .. reward.cashMinutes .. " min of cash")
	elseif reward.minCash or reward.cash then
		table.insert(parts, "💰 Cash")
	end
	if reward.boostMinutes then
		table.insert(parts, "🧪 2x Cash " .. reward.boostMinutes .. "m")
	end
	if reward.slimeMinRarity then
		table.insert(parts, "🟢 " .. reward.slimeMinRarity .. "+ slime")
	end
	return table.concat(parts, " + ")
end

return RewardDefs
