-- Mutations are rolled when a slime spawns. `chance` is the base probability;
-- "None" gets the remainder. Fusion upgrades a slime one step along `List`.
local Mutations = {}

Mutations.List = {
	{ key = "None", label = "", mult = 1, chance = 0, color = Color3.fromRGB(255, 255, 255) },
	{ key = "Golden", label = "Golden", mult = 2, chance = 0.06, color = Color3.fromRGB(255, 205, 60) },
	{ key = "Diamond", label = "Diamond", mult = 3, chance = 0.025, color = Color3.fromRGB(130, 235, 255) },
	{ key = "Rainbow", label = "Rainbow", mult = 5, chance = 0.01, color = Color3.fromRGB(255, 120, 220) },
	{ key = "Galaxy", label = "Galaxy", mult = 10, chance = 0.004, color = Color3.fromRGB(150, 90, 255) },
}

Mutations.ByKey = {}
for rank, def in Mutations.List do
	def.rank = rank
	Mutations.ByKey[def.key] = def
end

function Mutations.next(key: string): string
	local def = Mutations.ByKey[key] or Mutations.List[1]
	local nextDef = Mutations.List[math.min(def.rank + 1, #Mutations.List)]
	return nextDef.key
end

return Mutations
