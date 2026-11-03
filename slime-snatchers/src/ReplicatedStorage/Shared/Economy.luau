-- Pure economy formulas (no Roblox services) so server and client agree and the
-- logic can be unit-tested outside Roblox.
local Shared = script.Parent
local Config = require(Shared:WaitForChild("Config"))
local Slimes = require(Shared:WaitForChild("Slimes"))
local Rarities = require(Shared:WaitForChild("Rarities"))
local Mutations = require(Shared:WaitForChild("Mutations"))

local Tuning = Config.Tuning
local Economy = {}

Economy.LEVEL_MAX = 10

function Economy.levelMult(level: number): number
	return 1 + 0.15 * (math.clamp(level, 1, Economy.LEVEL_MAX) - 1)
end

-- Seconds a slime must sit on a pad to go from `level` to `level + 1`.
function Economy.xpToNext(level: number): number
	return 45 * level
end

function Economy.levelScale(level: number): number
	return 1 + 0.05 * (math.clamp(level, 1, Economy.LEVEL_MAX) - 1)
end

local function mutMult(key: string?): number
	local def = Mutations.ByKey[key or "None"]
	return if def then def.mult else 1
end

-- Base income per second of one slime, before player multipliers.
function Economy.slimeIncome(slime): number
	local def = Slimes.ById[slime.id]
	if not def then
		return 0
	end
	return def.income * mutMult(slime.mut) * Economy.levelMult(slime.lvl or 1)
end

function Economy.slimePrice(id: string, mut: string?): number
	local def = Slimes.ById[id]
	if not def then
		return math.huge
	end
	return def.price * mutMult(mut)
end

function Economy.sellValue(slime): number
	local def = Slimes.ById[slime.id]
	if not def then
		return 0
	end
	return math.floor(def.price * mutMult(slime.mut) * Economy.levelMult(slime.lvl or 1) * Tuning.SellRatio)
end

function Economy.rebirthCost(rebirths: number): number
	return 250000 * 12 ^ rebirths
end

function Economy.rebirthMult(rebirths: number): number
	return 1 + Tuning.RebirthCashMultEach * rebirths
end

function Economy.maxPads(rebirths: number, hasExtraPads: boolean): number
	local fromRebirth = math.min(rebirths * Tuning.PadsPerRebirth, Tuning.MaxRebirthPads)
	return Tuning.BasePads + fromRebirth + (if hasExtraPads then Tuning.ExtraPadsPass else 0)
end

function Economy.maxPossiblePads(): number
	return Tuning.BasePads + Tuning.MaxRebirthPads + Tuning.ExtraPadsPass
end

-- Cash granted by scaled products/rewards: N minutes of current income, with a floor.
function Economy.scaledCash(incomePerSecond: number, minutes: number, minimum: number): number
	return math.floor(math.max(minimum or 0, (incomePerSecond or 0) * 60 * (minutes or 0)))
end

-- Probability of each rarity at a given luck (1 = base). Returns { [key] = p }.
function Economy.rarityOdds(luck: number): { [string]: number }
	local total = 0
	local weights = {}
	for _, def in Rarities.List do
		local w = def.weight * (math.max(luck, 1) ^ def.luckPower)
		weights[def.key] = w
		total += w
	end
	local odds = {}
	for key, w in weights do
		odds[key] = w / total
	end
	return odds
end

-- Probability of each mutation when mutation chances are multiplied by `mult`.
function Economy.mutationOdds(mult: number): { [string]: number }
	local odds = {}
	local used = 0
	for _, def in Mutations.List do
		if def.key ~= "None" then
			local p = def.chance * math.max(mult, 1)
			odds[def.key] = p
			used += p
		end
	end
	if used > 0.6 then
		-- keep "None" as the majority outcome even under stacked boosts
		for key, p in odds do
			odds[key] = p * 0.6 / used
		end
		used = 0.6
	end
	odds.None = 1 - used
	return odds
end

local function pick(rng: Random, entries: { any }, weightOf: (any) -> number)
	local total = 0
	for _, e in entries do
		total += weightOf(e)
	end
	local roll = rng:NextNumber() * total
	for _, e in entries do
		roll -= weightOf(e)
		if roll <= 0 then
			return e
		end
	end
	return entries[#entries]
end

-- Rolls a slime id + mutation for the conveyor.
function Economy.rollSpawn(rng: Random, luck: number, mutationMult: number, minRank: number?)
	local odds = Economy.rarityOdds(luck)
	local candidates = {}
	for _, def in Rarities.List do
		if def.rank >= (minRank or 1) then
			table.insert(candidates, def)
		end
	end
	local rarity = pick(rng, candidates, function(def)
		return odds[def.key]
	end)
	local slime = pick(rng, Slimes.ByRarity[rarity.key], function(def)
		return def.weight
	end)
	local mutOdds = Economy.mutationOdds(mutationMult)
	local mutation = pick(rng, Mutations.List, function(def)
		return mutOdds[def.key]
	end)
	return slime.id, mutation.key
end

-- Offline earnings for a list of slimes.
function Economy.offlineEarnings(slimes: { any }, multiplier: number, seconds: number): number
	local perSecond = 0
	for _, slime in slimes do
		perSecond += Economy.slimeIncome(slime)
	end
	return math.floor(perSecond * multiplier * seconds * Tuning.OfflineEfficiency)
end

return Economy
