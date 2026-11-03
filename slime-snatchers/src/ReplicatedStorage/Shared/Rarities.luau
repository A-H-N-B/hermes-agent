-- Rarity tiers in ascending order. `weight` is the base spawn weight (sum = 10000,
-- so weight / 100 is the base percent). `luckPower` controls how strongly server
-- luck boosts the tier: effective weight = weight * luck ^ luckPower.
local Rarities = {}

Rarities.List = {
	{ key = "Common", color = Color3.fromRGB(196, 204, 214), weight = 5500, luckPower = 0, size = 2.3 },
	{ key = "Uncommon", color = Color3.fromRGB(100, 225, 120), weight = 2500, luckPower = 0.25, size = 2.6 },
	{ key = "Rare", color = Color3.fromRGB(70, 165, 255), weight = 1200, luckPower = 0.5, size = 2.9 },
	{ key = "Epic", color = Color3.fromRGB(180, 95, 255), weight = 500, luckPower = 1, size = 3.2 },
	{ key = "Legendary", color = Color3.fromRGB(255, 196, 45), weight = 200, luckPower = 1, size = 3.6 },
	{ key = "Mythic", color = Color3.fromRGB(255, 72, 112), weight = 70, luckPower = 1, size = 4.0 },
	{ key = "Celestial", color = Color3.fromRGB(110, 255, 235), weight = 24, luckPower = 1, size = 4.4 },
	{ key = "Secret", color = Color3.fromRGB(255, 255, 255), weight = 6, luckPower = 1, size = 5.0, rainbowText = true },
}

Rarities.ByKey = {}
for rank, def in Rarities.List do
	def.rank = rank
	Rarities.ByKey[def.key] = def
end

return Rarities
