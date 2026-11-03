-- Slime catalog (original characters). Within a rarity, `weight` picks which
-- slime spawns (cheaper ones are more common). Payback (price / income) grows
-- from ~25s for commons to ~140s for secrets so buying always pays off and
-- pad space becomes the real bottleneck.
--
-- accessory: none | leaf | bow | horns | crown | halo | antenna | spikes | ears | hat | flame
local Slimes = {}

local function rgb(r, g, b)
	return Color3.fromRGB(r, g, b)
end

Slimes.List = {
	-- Common
	{ id = "blubby", name = "Blubby", rarity = "Common", price = 25, income = 1, weight = 50,
		body = rgb(120, 220, 110), accent = rgb(80, 170, 70), accessory = "none" },
	{ id = "gloopy", name = "Gloopy", rarity = "Common", price = 60, income = 2, weight = 33,
		body = rgb(110, 190, 255), accent = rgb(60, 130, 220), accessory = "antenna" },
	{ id = "sprout", name = "Sprout Slime", rarity = "Common", price = 120, income = 4, weight = 17,
		body = rgb(170, 230, 90), accent = rgb(60, 160, 50), accessory = "leaf" },
	-- Uncommon
	{ id = "bubblegum", name = "Bubblegum Bop", rarity = "Uncommon", price = 400, income = 10, weight = 50,
		body = rgb(255, 150, 210), accent = rgb(230, 90, 170), accessory = "bow" },
	{ id = "minty", name = "Minty Wobble", rarity = "Uncommon", price = 750, income = 17, weight = 33,
		body = rgb(150, 255, 215), accent = rgb(60, 200, 160), accessory = "ears" },
	{ id = "puddle", name = "Puddle Pup", rarity = "Uncommon", price = 1200, income = 25, weight = 17,
		body = rgb(120, 160, 255), accent = rgb(70, 90, 200), accessory = "ears" },
	-- Rare
	{ id = "lava", name = "Lava Lump", rarity = "Rare", price = 3500, income = 60, weight = 50,
		body = rgb(255, 110, 40), accent = rgb(200, 40, 20), accessory = "flame", material = "Neon" },
	{ id = "frosty", name = "Frosty Flop", rarity = "Rare", price = 6000, income = 95, weight = 33,
		body = rgb(190, 240, 255), accent = rgb(120, 200, 255), accessory = "spikes", material = "Ice" },
	{ id = "zappy", name = "Zappy Jelly", rarity = "Rare", price = 9000, income = 130, weight = 17,
		body = rgb(255, 240, 80), accent = rgb(255, 180, 0), accessory = "antenna", material = "Neon" },
	-- Epic
	{ id = "noodle", name = "Neon Noodle", rarity = "Epic", price = 35000, income = 450, weight = 50,
		body = rgb(80, 255, 200), accent = rgb(255, 60, 220), accessory = "antenna", material = "Neon" },
	{ id = "choco", name = "Choco Chomper", rarity = "Epic", price = 60000, income = 700, weight = 33,
		body = rgb(130, 80, 45), accent = rgb(255, 230, 200), accessory = "hat" },
	{ id = "ghostly", name = "Ghostly Goo", rarity = "Epic", price = 95000, income = 1000, weight = 17,
		body = rgb(235, 235, 255), accent = rgb(170, 160, 255), accessory = "none", material = "ForceField" },
	-- Legendary
	{ id = "squish", name = "Sir Squish", rarity = "Legendary", price = 450000, income = 5000, weight = 50,
		body = rgb(90, 120, 255), accent = rgb(255, 210, 60), accessory = "crown" },
	{ id = "mochi", name = "Mochi Moon", rarity = "Legendary", price = 750000, income = 7500, weight = 33,
		body = rgb(255, 240, 245), accent = rgb(255, 170, 200), accessory = "ears" },
	{ id = "thunder", name = "Thunder Blob", rarity = "Legendary", price = 1200000, income = 11000, weight = 17,
		body = rgb(60, 60, 110), accent = rgb(255, 250, 90), accessory = "spikes", material = "Neon" },
	-- Mythic
	{ id = "crystal", name = "Crystal Queen", rarity = "Mythic", price = 6000000, income = 55000, weight = 50,
		body = rgb(200, 150, 255), accent = rgb(255, 255, 255), accessory = "crown", material = "Glass" },
	{ id = "dragon", name = "Dragon Drip", rarity = "Mythic", price = 10000000, income = 85000, weight = 33,
		body = rgb(220, 40, 60), accent = rgb(255, 200, 60), accessory = "horns" },
	{ id = "cosmo", name = "Cosmo Goo", rarity = "Mythic", price = 16000000, income = 125000, weight = 17,
		body = rgb(40, 20, 90), accent = rgb(120, 220, 255), accessory = "antenna", material = "Neon" },
	-- Celestial
	{ id = "angel", name = "Angel Ooze", rarity = "Celestial", price = 110000000, income = 900000, weight = 50,
		body = rgb(255, 255, 230), accent = rgb(255, 230, 120), accessory = "halo" },
	{ id = "solar", name = "Solar Splat", rarity = "Celestial", price = 180000000, income = 1400000, weight = 33,
		body = rgb(255, 170, 30), accent = rgb(255, 90, 0), accessory = "flame", material = "Neon" },
	{ id = "nebula", name = "Nebula Nibbler", rarity = "Celestial", price = 280000000, income = 2000000, weight = 17,
		body = rgb(120, 60, 200), accent = rgb(255, 120, 230), accessory = "halo", material = "Neon" },
	-- Secret
	{ id = "gloopus", name = "King Gloopus", rarity = "Secret", price = 3000000000, income = 22000000, weight = 50,
		body = rgb(30, 200, 90), accent = rgb(255, 215, 0), accessory = "crown", material = "Neon" },
	{ id = "void", name = "Void Wobbler", rarity = "Secret", price = 5000000000, income = 34000000, weight = 33,
		body = rgb(15, 10, 25), accent = rgb(170, 0, 255), accessory = "horns", material = "Neon" },
	{ id = "overlord", name = "Prism Overlord", rarity = "Secret", price = 8000000000, income = 50000000, weight = 17,
		body = rgb(255, 255, 255), accent = rgb(255, 0, 170), accessory = "halo", material = "Glass" },
}

Slimes.ById = {}
Slimes.ByRarity = {}
for index, def in Slimes.List do
	def.index = index
	Slimes.ById[def.id] = def
	Slimes.ByRarity[def.rarity] = Slimes.ByRarity[def.rarity] or {}
	table.insert(Slimes.ByRarity[def.rarity], def)
end

return Slimes
