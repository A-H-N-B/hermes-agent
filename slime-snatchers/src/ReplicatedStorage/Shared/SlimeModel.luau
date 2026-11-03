-- Procedural slime models (no uploaded assets needed). A slime is an anchored
-- ellipsoid "Body" with features welded to it, so moving/scaling the Body moves
-- the whole creature.
local Shared = script.Parent
local New = require(Shared:WaitForChild("New"))
local Slimes = require(Shared:WaitForChild("Slimes"))
local Rarities = require(Shared:WaitForChild("Rarities"))
local Mutations = require(Shared:WaitForChild("Mutations"))
local Economy = require(Shared:WaitForChild("Economy"))
local Format = require(Shared:WaitForChild("Format"))

local SlimeModel = {}

local FONT = Enum.Font.FredokaOne
local WHITE = Color3.new(1, 1, 1)
local BLACK = Color3.fromRGB(25, 20, 30)

local function part(props)
	props.Anchored = false
	props.CanCollide = false
	props.CanTouch = false
	props.CanQuery = false
	props.Massless = true
	props.CastShadow = false
	props.TopSurface = Enum.SurfaceType.Smooth
	props.BottomSurface = Enum.SurfaceType.Smooth
	return New("Part", props)
end

local function weldTo(body: BasePart, piece: BasePart, offset: CFrame)
	piece.CFrame = body.CFrame * offset
	New("WeldConstraint", { Part0 = body, Part1 = piece, Parent = piece })
	piece.Parent = body.Parent
end

local function ball(size: number, color: Color3, material: Enum.Material?)
	return part({
		Shape = Enum.PartType.Ball,
		Size = Vector3.new(size, size, size),
		Color = color,
		Material = material or Enum.Material.SmoothPlastic,
	})
end

local function ellipsoid(size: Vector3, color: Color3, material: Enum.Material?)
	local p = part({ Size = size, Color = color, Material = material or Enum.Material.SmoothPlastic })
	New("SpecialMesh", { MeshType = Enum.MeshType.Sphere, Parent = p })
	return p
end

local ACCESSORIES = {}

function ACCESSORIES.leaf(body, s, def)
	local leaf = ellipsoid(Vector3.new(s * 0.12, s * 0.45, s * 0.3), def.accent)
	weldTo(body, leaf, CFrame.new(0, s * 0.5, 0) * CFrame.Angles(0, 0, math.rad(25)))
end

function ACCESSORIES.bow(body, s, def)
	for _, side in { -1, 1 } do
		local loop = ellipsoid(Vector3.new(s * 0.28, s * 0.2, s * 0.12), def.accent)
		weldTo(body, loop, CFrame.new(side * s * 0.16, s * 0.42, -s * 0.05) * CFrame.Angles(0, 0, math.rad(side * 20)))
	end
	weldTo(body, ball(s * 0.1, def.accent), CFrame.new(0, s * 0.42, -s * 0.05))
end

function ACCESSORIES.antenna(body, s, def)
	local stick = part({ Size = Vector3.new(s * 0.05, s * 0.4, s * 0.05), Color = def.accent })
	weldTo(body, stick, CFrame.new(0, s * 0.55, 0))
	weldTo(body, ball(s * 0.16, def.accent, Enum.Material.Neon), CFrame.new(0, s * 0.78, 0))
end

function ACCESSORIES.crown(body, s, _def)
	local gold = Color3.fromRGB(255, 210, 60)
	local band = part({ Shape = Enum.PartType.Cylinder, Size = Vector3.new(s * 0.18, s * 0.55, s * 0.55), Color = gold,
		Material = Enum.Material.Metal })
	weldTo(body, band, CFrame.new(0, s * 0.42, 0) * CFrame.Angles(0, 0, math.rad(90)))
	for i = 0, 4 do
		local angle = math.rad(i * 72)
		local spike = part({ Size = Vector3.new(s * 0.08, s * 0.2, s * 0.08), Color = gold, Material = Enum.Material.Metal })
		weldTo(body, spike, CFrame.new(math.cos(angle) * s * 0.22, s * 0.58, math.sin(angle) * s * 0.22))
	end
	weldTo(body, ball(s * 0.09, Color3.fromRGB(255, 60, 90), Enum.Material.Neon), CFrame.new(0, s * 0.45, -s * 0.28))
end

function ACCESSORIES.halo(body, s, _def)
	local halo = part({ Shape = Enum.PartType.Cylinder, Size = Vector3.new(s * 0.05, s * 0.7, s * 0.7),
		Color = Color3.fromRGB(255, 240, 150), Material = Enum.Material.Neon, Transparency = 0.15 })
	weldTo(body, halo, CFrame.new(0, s * 0.75, 0) * CFrame.Angles(0, 0, math.rad(90)))
end

function ACCESSORIES.horns(body, s, def)
	for _, side in { -1, 1 } do
		local horn = part({ Size = Vector3.new(s * 0.1, s * 0.32, s * 0.1), Color = def.accent })
		weldTo(body, horn, CFrame.new(side * s * 0.22, s * 0.48, 0) * CFrame.Angles(0, 0, math.rad(-side * 25)))
	end
end

function ACCESSORIES.spikes(body, s, def)
	for i = -1, 1 do
		local spike = part({ Size = Vector3.new(s * 0.1, s * 0.25, s * 0.1), Color = def.accent })
		weldTo(body, spike, CFrame.new(0, s * 0.44, i * s * 0.2) * CFrame.Angles(math.rad(i * 25), 0, 0))
	end
end

function ACCESSORIES.ears(body, s, def)
	for _, side in { -1, 1 } do
		weldTo(body, ball(s * 0.22, def.accent), CFrame.new(side * s * 0.3, s * 0.36, 0))
	end
end

function ACCESSORIES.hat(body, s, def)
	local brim = part({ Shape = Enum.PartType.Cylinder, Size = Vector3.new(s * 0.05, s * 0.6, s * 0.6), Color = BLACK })
	weldTo(body, brim, CFrame.new(0, s * 0.4, 0) * CFrame.Angles(0, 0, math.rad(90)))
	local top = part({ Shape = Enum.PartType.Cylinder, Size = Vector3.new(s * 0.35, s * 0.38, s * 0.38), Color = BLACK })
	weldTo(body, top, CFrame.new(0, s * 0.6, 0) * CFrame.Angles(0, 0, math.rad(90)))
	local band = part({ Shape = Enum.PartType.Cylinder, Size = Vector3.new(s * 0.08, s * 0.39, s * 0.39), Color = def.accent })
	weldTo(body, band, CFrame.new(0, s * 0.47, 0) * CFrame.Angles(0, 0, math.rad(90)))
end

function ACCESSORIES.flame(body, s, def)
	local holder = part({ Size = Vector3.new(0.2, 0.2, 0.2), Transparency = 1 })
	weldTo(body, holder, CFrame.new(0, s * 0.5, 0))
	New("Fire", { Size = s * 1.2, Heat = 4, Color = def.accent, SecondaryColor = def.body, Parent = holder })
end

local function applyMutation(model: Model, body: BasePart, mutKey: string, s: number)
	if mutKey == "Golden" then
		body.Color = Color3.fromRGB(255, 205, 60)
		body.Material = Enum.Material.SmoothPlastic
		body.Reflectance = 0.25
		New("Sparkles", { SparkleColor = Color3.fromRGB(255, 230, 120), Parent = body })
	elseif mutKey == "Diamond" then
		body.Color = Color3.fromRGB(170, 240, 255)
		body.Material = Enum.Material.Glass
		body.Reflectance = 0.35
		New("Sparkles", { SparkleColor = Color3.fromRGB(180, 250, 255), Parent = body })
	elseif mutKey == "Rainbow" then
		-- the client cycles the hue for every model with this attribute
		model:SetAttribute("Rainbow", true)
		body.Material = Enum.Material.Neon
	elseif mutKey == "Galaxy" then
		body.Color = Color3.fromRGB(60, 25, 120)
		body.Material = Enum.Material.Neon
		New("ParticleEmitter", {
			Rate = 12,
			Lifetime = NumberRange.new(1, 2),
			Speed = NumberRange.new(0.5, 1.5),
			Size = NumberSequence.new(s * 0.12),
			LightEmission = 1,
			Color = ColorSequence.new(Color3.fromRGB(200, 150, 255), Color3.fromRGB(80, 200, 255)),
			Parent = body,
		})
	end
end

local function label(text: string, color: Color3, order: number, height: number)
	return New("TextLabel", {
		Name = "L" .. order,
		BackgroundTransparency = 1,
		Size = UDim2.fromScale(1, height),
		Font = FONT,
		Text = text,
		TextColor3 = color,
		TextScaled = true,
		TextStrokeTransparency = 0.15,
		TextStrokeColor3 = BLACK,
		LayoutOrder = order,
	})
end

-- Billboard info: { price = number?, level = number?, owner = string? }
function SlimeModel.makeBillboard(model: Model, info)
	local body = model.PrimaryPart :: BasePart
	local id = model:GetAttribute("SlimeId")
	local mutKey = model:GetAttribute("Mutation") or "None"
	local def = Slimes.ById[id]
	local rarity = Rarities.ByKey[def.rarity]
	local mut = Mutations.ByKey[mutKey]
	local s = rarity.size

	local old = body:FindFirstChild("Nameplate")
	if old then
		old:Destroy()
	end
	local gui = New("BillboardGui", {
		Name = "Nameplate",
		Size = UDim2.fromScale(7, 3.2),
		StudsOffsetWorldSpace = Vector3.new(0, s * 0.55 + 2.2, 0),
		MaxDistance = 75,
		LightInfluence = 0,
		ResetOnSpawn = false,
		Adornee = body,
	})
	New("UIListLayout", {
		SortOrder = Enum.SortOrder.LayoutOrder,
		VerticalAlignment = Enum.VerticalAlignment.Bottom,
		HorizontalAlignment = Enum.HorizontalAlignment.Center,
		Parent = gui,
	})
	if mut and mut.key ~= "None" then
		label(mut.label, mut.color, 1, 0.2).Parent = gui
	end
	label(def.name, WHITE, 2, 0.24).Parent = gui
	local rarityLabel = label(def.rarity, rarity.color, 3, 0.18)
	rarityLabel.Parent = gui
	if rarity.rainbowText then
		New("UIGradient", {
			Color = ColorSequence.new({
				ColorSequenceKeypoint.new(0, Color3.fromRGB(255, 80, 80)),
				ColorSequenceKeypoint.new(0.33, Color3.fromRGB(255, 240, 80)),
				ColorSequenceKeypoint.new(0.66, Color3.fromRGB(80, 255, 160)),
				ColorSequenceKeypoint.new(1, Color3.fromRGB(120, 120, 255)),
			}),
			Parent = rarityLabel,
		})
	end
	local slime = { id = id, mut = mutKey, lvl = info.level or 1 }
	label(Format.rate(Economy.slimeIncome(slime)), Color3.fromRGB(120, 255, 120), 4, 0.18).Parent = gui
	if info.price then
		label(Format.cash(info.price), Color3.fromRGB(255, 230, 90), 5, 0.2).Parent = gui
	elseif info.level then
		local text = "Lv " .. info.level .. (if info.level >= Economy.LEVEL_MAX then " MAX" else "")
		label(text, Color3.fromRGB(180, 220, 255), 5, 0.16).Parent = gui
	end
	gui.Parent = body
	return gui
end

-- mode: "belt" | "pad" | "carry"
function SlimeModel.build(id: string, mutKey: string, level: number?, mode: string?): Model
	local def = Slimes.ById[id]
	assert(def, "unknown slime " .. tostring(id))
	local rarity = Rarities.ByKey[def.rarity]
	local s = rarity.size

	local model = New("Model", { Name = def.name })
	model:SetAttribute("SlimeId", id)
	model:SetAttribute("Mutation", mutKey)
	model:SetAttribute("Rarity", def.rarity)

	local material = if def.material then (Enum.Material :: any)[def.material] else Enum.Material.SmoothPlastic
	local body = ellipsoid(Vector3.new(s * 1.15, s * 0.85, s * 1.15), def.body, material)
	body.Name = "Body"
	body.Anchored = true
	body.Massless = false
	body.CanQuery = true
	body.Transparency = if material == Enum.Material.Glass then 0.25 else 0.05
	body.Parent = model
	model.PrimaryPart = body

	-- glossy highlight + face
	weldTo(body, ball(s * 0.18, WHITE, Enum.Material.Neon), CFrame.new(-s * 0.25, s * 0.25, -s * 0.3))
	for _, side in { -1, 1 } do
		weldTo(body, ball(s * 0.3, WHITE), CFrame.new(side * s * 0.2, s * 0.08, -s * 0.47))
		weldTo(body, ball(s * 0.16, BLACK), CFrame.new(side * s * 0.2, s * 0.06, -s * 0.6))
		weldTo(body, ball(s * 0.05, WHITE, Enum.Material.Neon), CFrame.new(side * s * 0.17, s * 0.1, -s * 0.67))
		local cheek = ellipsoid(Vector3.new(s * 0.16, s * 0.08, s * 0.05), Color3.fromRGB(255, 140, 170))
		cheek.Transparency = 0.3
		weldTo(body, cheek, CFrame.new(side * s * 0.36, -s * 0.08, -s * 0.47))
	end
	local mouth = ellipsoid(Vector3.new(s * 0.14, s * 0.07, s * 0.05), BLACK)
	weldTo(body, mouth, CFrame.new(0, -s * 0.12, -s * 0.55))

	local accessory = ACCESSORIES[def.accessory]
	if accessory then
		accessory(body, s, def)
	end
	applyMutation(model, body, mutKey, s)

	if rarity.rank >= 5 then
		New("PointLight", { Color = rarity.color, Range = s * 3, Brightness = 1.2, Parent = body })
	end

	if level and level > 1 then
		model:ScaleTo(Economy.levelScale(level))
	end
	model:SetAttribute("Level", level or 1)
	if mode ~= "carry" then
		SlimeModel.makeBillboard(model, { level = if mode == "pad" then (level or 1) else nil })
	end
	return model
end

function SlimeModel.setLevel(model: Model, level: number)
	model:ScaleTo(Economy.levelScale(level))
	model:SetAttribute("Level", level)
	SlimeModel.makeBillboard(model, { level = level })
end

-- Height of the body's bottom above its pivot, used to rest slimes on surfaces.
function SlimeModel.restHeight(model: Model): number
	local body = model.PrimaryPart :: BasePart
	return body.Size.Y / 2
end

return SlimeModel
