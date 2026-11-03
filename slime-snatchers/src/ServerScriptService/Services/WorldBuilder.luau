-- Builds the whole map from code at server start, so the game works from a
-- blank place. Layout (top view, +Z = towards the goo pit):
--
--   [Boards]      [ SLIME PIPE ]                 z = -165
--   Plot1  Plot5      | belt |                   plots: x = -52 (west) / +52 (east)
--   Plot2  Plot6   shop  |  shop                 z centers: -108, -36, 36, 108
--   Plot3  Plot7      |      |
--   Plot4  Plot8   [ GOO PIT ]                   z = +165
local Lighting = game:GetService("Lighting")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local New = require(Shared:WaitForChild("New"))
local Config = require(Shared:WaitForChild("Config"))
local Economy = require(Shared:WaitForChild("Economy"))

local WorldBuilder = {}

local PLOT_W = 64
local PLOT_D = 84
local ENTRANCE_X = 52
local PLOT_Z = { -108, -36, 36, 108 }
local PAD_COLUMNS = { -18, 0, 18 }
local PAD_ROW_START = 14
local PAD_ROW_GAP = 7
local FONT = Enum.Font.FredokaOne

WorldBuilder.BeltStart = Vector3.new(0, 1.5, -150)
WorldBuilder.BeltEnd = Vector3.new(0, 1.5, 150)
WorldBuilder.Plots = {}

local function block(props)
	props.Anchored = true
	props.TopSurface = Enum.SurfaceType.Smooth
	props.BottomSurface = Enum.SurfaceType.Smooth
	if props.Material == nil then
		props.Material = Enum.Material.SmoothPlastic
	end
	return New("Part", props)
end

local function textSurface(part: BasePart, text: string, color: Color3, face: Enum.NormalId?)
	local gui = New("SurfaceGui", {
		Face = face or Enum.NormalId.Front,
		SizingMode = Enum.SurfaceGuiSizingMode.PixelsPerStud,
		PixelsPerStud = 40,
		LightInfluence = 0,
		Parent = part,
	})
	local label = New("TextLabel", {
		Name = "Text",
		BackgroundTransparency = 1,
		Size = UDim2.fromScale(1, 1),
		Font = FONT,
		Text = text,
		TextColor3 = color,
		TextScaled = true,
		Parent = gui,
	})
	New("UIStroke", { Thickness = 3, Color = Color3.fromRGB(20, 20, 30), Parent = label })
	return label
end

local function billboard(adornee: BasePart, size: UDim2, offset: Vector3, text: string, color: Color3, maxDistance: number?)
	local gui = New("BillboardGui", {
		Size = size,
		StudsOffsetWorldSpace = offset,
		MaxDistance = maxDistance or 80,
		LightInfluence = 0,
		Adornee = adornee,
		Parent = adornee,
	})
	local label = New("TextLabel", {
		Name = "Text",
		BackgroundTransparency = 1,
		Size = UDim2.fromScale(1, 1),
		Font = FONT,
		Text = text,
		TextColor3 = color,
		TextScaled = true,
		TextStrokeTransparency = 0.1,
		Parent = gui,
	})
	return gui, label
end

local function setupLighting()
	Lighting.ClockTime = 14.5
	Lighting.Brightness = 2.4
	Lighting.Ambient = Color3.fromRGB(120, 115, 140)
	Lighting.OutdoorAmbient = Color3.fromRGB(160, 155, 175)
	Lighting.EnvironmentDiffuseScale = 0.6
	Lighting.EnvironmentSpecularScale = 0.7
	Lighting.GlobalShadows = true
	for _, child in Lighting:GetChildren() do
		if child:IsA("PostEffect") or child:IsA("Atmosphere") or child:IsA("Sky") then
			child:Destroy()
		end
	end
	New("Atmosphere", {
		Density = 0.28,
		Offset = 0.2,
		Color = Color3.fromRGB(200, 215, 255),
		Decay = Color3.fromRGB(150, 120, 200),
		Glare = 0.2,
		Haze = 1.2,
		Parent = Lighting,
	})
	New("Sky", { Name = "Sky", Parent = Lighting })
	New("BloomEffect", { Intensity = 0.5, Size = 28, Threshold = 0.92, Parent = Lighting })
	New("ColorCorrectionEffect", { Name = "Grade", Saturation = 0.18, Contrast = 0.06, Parent = Lighting })
	New("SunRaysEffect", { Intensity = 0.04, Spread = 0.6, Parent = Lighting })
end

local function tree(parent: Instance, position: Vector3, scale: number)
	block({
		Name = "Trunk",
		Shape = Enum.PartType.Cylinder,
		Size = Vector3.new(8 * scale, 2 * scale, 2 * scale),
		CFrame = CFrame.new(position + Vector3.new(0, 4 * scale, 0)) * CFrame.Angles(0, 0, math.rad(90)),
		Color = Color3.fromRGB(120, 80, 50),
		Material = Enum.Material.Wood,
		Parent = parent,
	})
	local hue = math.random(25, 40) / 100
	block({
		Name = "Leaves",
		Shape = Enum.PartType.Ball,
		Size = Vector3.new(9 * scale, 9 * scale, 9 * scale),
		CFrame = CFrame.new(position + Vector3.new(0, 10 * scale, 0)),
		Color = Color3.fromHSV(hue, 0.6, 0.75),
		Material = Enum.Material.Grass,
		CanCollide = false,
		Parent = parent,
	})
end

local function lamp(parent: Instance, position: Vector3)
	block({
		Name = "Post",
		Size = Vector3.new(0.8, 12, 0.8),
		CFrame = CFrame.new(position + Vector3.new(0, 6, 0)),
		Color = Color3.fromRGB(60, 60, 75),
		Material = Enum.Material.Metal,
		Parent = parent,
	})
	local bulb = block({
		Name = "Bulb",
		Shape = Enum.PartType.Ball,
		Size = Vector3.new(2.2, 2.2, 2.2),
		CFrame = CFrame.new(position + Vector3.new(0, 12.6, 0)),
		Color = Color3.fromRGB(255, 235, 170),
		Material = Enum.Material.Neon,
		CanCollide = false,
		Parent = parent,
	})
	New("PointLight", { Range = 22, Brightness = 1.4, Color = Color3.fromRGB(255, 225, 170), Parent = bulb })
end

local function buildGround(world: Instance)
	block({
		Name = "Ground",
		Size = Vector3.new(340, 4, 440),
		CFrame = CFrame.new(0, -2, 0),
		Color = Color3.fromRGB(110, 200, 95),
		Material = Enum.Material.Grass,
		Parent = world,
	})
	-- walkway strips along the belt
	for _, x in { -28, 28 } do
		block({
			Name = "Walkway",
			Size = Vector3.new(36, 0.2, 300),
			CFrame = CFrame.new(x, 0.1, 0),
			Color = Color3.fromRGB(235, 225, 200),
			Material = Enum.Material.Pavement,
			Parent = world,
		})
	end
	-- invisible boundary
	for _, data in {
		{ Vector3.new(340, 60, 2), Vector3.new(0, 30, -221) },
		{ Vector3.new(340, 60, 2), Vector3.new(0, 30, 221) },
		{ Vector3.new(2, 60, 440), Vector3.new(-171, 30, 0) },
		{ Vector3.new(2, 60, 440), Vector3.new(171, 30, 0) },
	} do
		block({ Name = "Boundary", Size = data[1], CFrame = CFrame.new(data[2]), Transparency = 1, Parent = world })
	end
	for z = -200, 200, 28 do
		tree(world, Vector3.new(-160, 0, z), 0.9 + math.random() * 0.4)
		tree(world, Vector3.new(160, 0, z), 0.9 + math.random() * 0.4)
	end
	for z = -140, 140, 35 do
		lamp(world, Vector3.new(-12, 0, z))
		lamp(world, Vector3.new(12, 0, z))
	end
	New("SpawnLocation", {
		Name = "FallbackSpawn",
		Anchored = true,
		Size = Vector3.new(10, 1, 10),
		CFrame = CFrame.new(0, 0.5, 192),
		Color = Color3.fromRGB(255, 255, 255),
		Material = Enum.Material.SmoothPlastic,
		Duration = 0,
		Neutral = true,
		Parent = world,
	})
end

local function buildBelt(world: Instance)
	local belt = New("Model", { Name = "Belt", Parent = world })
	local length = (WorldBuilder.BeltEnd - WorldBuilder.BeltStart).Magnitude
	block({
		Name = "Surface",
		Size = Vector3.new(14, 1.5, length),
		CFrame = CFrame.new(0, 0.75, 0),
		Color = Color3.fromRGB(55, 50, 70),
		Material = Enum.Material.DiamondPlate,
		Parent = belt,
	})
	for _, x in { -7.4, 7.4 } do
		block({
			Name = "Rail",
			Size = Vector3.new(0.8, 2, length),
			CFrame = CFrame.new(x, 1.5, 0),
			Color = Color3.fromRGB(90, 255, 140),
			Material = Enum.Material.Neon,
			Parent = belt,
		})
	end

	-- the Slime Pipe machine (spawn point)
	local pipe = New("Model", { Name = "SlimePipe", Parent = world })
	local housing = block({
		Name = "Housing",
		Size = Vector3.new(30, 22, 16),
		CFrame = CFrame.new(0, 11, -166),
		Color = Color3.fromRGB(120, 70, 200),
		Parent = pipe,
	})
	block({
		Name = "Outlet",
		Shape = Enum.PartType.Cylinder,
		Size = Vector3.new(6, 11, 11),
		CFrame = CFrame.new(0, 6.5, -156) * CFrame.Angles(0, math.rad(90), 0),
		Color = Color3.fromRGB(80, 255, 150),
		Material = Enum.Material.Neon,
		Parent = pipe,
	})
	local sign = block({
		Name = "Sign",
		Size = Vector3.new(26, 7, 1),
		CFrame = CFrame.new(0, 17, -157.6) * CFrame.Angles(0, math.rad(180), 0),
		Color = Color3.fromRGB(40, 25, 70),
		Parent = pipe,
	})
	textSurface(sign, "🟢 SLIME PIPE 🟢", Color3.fromRGB(150, 255, 180))
	New("ProximityPrompt", {
		Name = "OddsPrompt",
		ActionText = "View Odds",
		ObjectText = "Slime Pipe",
		HoldDuration = 0,
		MaxActivationDistance = 22,
		RequiresLineOfSight = false,
		Parent = housing,
	}):SetAttribute("OpenPanel", "Odds")

	-- goo pit at the end of the belt
	block({
		Name = "GooPit",
		Size = Vector3.new(22, 1, 18),
		CFrame = CFrame.new(0, 0.3, 162),
		Color = Color3.fromRGB(60, 255, 120),
		Material = Enum.Material.Neon,
		Transparency = 0.2,
		Parent = world,
	})

	-- shop kiosks
	for _, x in { -30, 30 } do
		local kiosk = block({
			Name = "ShopKiosk",
			Size = Vector3.new(10, 8, 10),
			CFrame = CFrame.new(x, 4, 0),
			Color = Color3.fromRGB(255, 170, 60),
			Parent = world,
		})
		block({
			Name = "Roof",
			Size = Vector3.new(13, 1, 13),
			CFrame = CFrame.new(x, 8.5, 0),
			Color = Color3.fromRGB(255, 80, 120),
			Parent = world,
		})
		billboard(kiosk, UDim2.fromScale(10, 3), Vector3.new(0, 8, 0), "🛒 SHOP", Color3.fromRGB(255, 240, 120), 120)
		New("ProximityPrompt", {
			Name = "ShopPrompt",
			ActionText = "Open Shop",
			ObjectText = "Slime Shop",
			HoldDuration = 0,
			MaxActivationDistance = 14,
			RequiresLineOfSight = false,
			Parent = kiosk,
		}):SetAttribute("OpenPanel", "Shop")
	end

	-- leaderboard boards (filled by LeaderboardService)
	local boards = New("Folder", { Name = "Leaderboards", Parent = world })
	for i, info in { { "Earned", "💰 TOP EARNERS", -34 }, { "Rebirths", "⭐ TOP REBIRTHS", 34 } } do
		local board = block({
			Name = info[1],
			Size = Vector3.new(22, 28, 1),
			CFrame = CFrame.new(info[3], 15, -178),
			Color = Color3.fromRGB(35, 30, 55),
			Parent = boards,
		})
		board:SetAttribute("Title", info[2])
		board:SetAttribute("Order", i)
		block({
			Name = "Stand",
			Size = Vector3.new(2, 2, 2),
			CFrame = CFrame.new(info[3], 1, -178),
			Color = Color3.fromRGB(60, 55, 80),
			Parent = boards,
		})
	end
end

local function buildPlot(world: Instance, index: number, entrance: Vector3, inward: Vector3)
	local cf = CFrame.lookAt(entrance, entrance + inward)
	local model = New("Model", { Name = "Plot" .. index })
	model:SetAttribute("PlotIndex", index)
	model:SetAttribute("OwnerId", 0)
	local function at(x: number, y: number, depth: number): CFrame
		return cf * CFrame.new(x, y, -depth)
	end
	local hue = (index - 1) / 8
	local wallColor = Color3.fromHSV(hue, 0.35, 0.95)

	block({
		Name = "Floor",
		Size = Vector3.new(PLOT_W, 1, PLOT_D),
		CFrame = at(0, 0.5, PLOT_D / 2),
		Color = Color3.fromHSV(hue, 0.12, 0.98),
		Parent = model,
	})
	-- walls: back, sides and the two front pieces around a 20-stud entrance
	local wallH = 12
	for _, w in {
		{ Vector3.new(PLOT_W, wallH, 1.5), at(0, wallH / 2 + 1, PLOT_D) },
		{ Vector3.new(1.5, wallH, PLOT_D), at(-PLOT_W / 2, wallH / 2 + 1, PLOT_D / 2) },
		{ Vector3.new(1.5, wallH, PLOT_D), at(PLOT_W / 2, wallH / 2 + 1, PLOT_D / 2) },
		{ Vector3.new(22, wallH, 1.5), at(-21, wallH / 2 + 1, 0) },
		{ Vector3.new(22, wallH, 1.5), at(21, wallH / 2 + 1, 0) },
	} do
		block({ Name = "Wall", Size = w[1], CFrame = w[2], Color = wallColor, Transparency = 0.1, Parent = model })
	end

	local barrier = block({
		Name = "Barrier",
		Size = Vector3.new(20, wallH, 1),
		CFrame = at(0, wallH / 2 + 1, 0),
		Color = Color3.fromRGB(255, 60, 80),
		Material = Enum.Material.ForceField,
		Transparency = 1,
		CanCollide = false,
		Parent = model,
	})

	local lockButton = block({
		Name = "LockButton",
		Shape = Enum.PartType.Cylinder,
		Size = Vector3.new(0.8, 6, 6),
		CFrame = at(-20, 1.3, 7) * CFrame.Angles(0, 0, math.rad(90)),
		Color = Color3.fromRGB(255, 70, 90),
		Material = Enum.Material.Neon,
		Parent = model,
	})
	local _, lockLabel = billboard(lockButton, UDim2.fromScale(8, 2.4), Vector3.new(0, 3.5, 0), "🔒 LOCK BASE",
		Color3.fromRGB(255, 255, 255), 60)

	local signPart = block({
		Name = "Sign",
		Size = Vector3.new(26, 5, 1),
		CFrame = at(0, wallH + 4, -0.2) * CFrame.Angles(0, math.rad(180), 0),
		Color = Color3.fromHSV(hue, 0.6, 0.55),
		Parent = model,
	})
	local signLabel = textSurface(signPart, "Empty Base", Color3.fromRGB(255, 255, 255))

	local pads = {}
	local padFolder = New("Folder", { Name = "Pads", Parent = model })
	local maxPads = Economy.maxPossiblePads()
	for i = 1, maxPads do
		local col = PAD_COLUMNS[(i - 1) % #PAD_COLUMNS + 1]
		local row = (i - 1) // #PAD_COLUMNS
		local depth = PAD_ROW_START + row * PAD_ROW_GAP
		local pad = block({
			Name = "Pad" .. i,
			Size = Vector3.new(6.5, 0.6, 6.5),
			CFrame = at(col, 1.3, depth),
			Color = Color3.fromRGB(245, 245, 250),
			Parent = padFolder,
		})
		local glow = block({
			Name = "Glow",
			Size = Vector3.new(7.2, 0.3, 7.2),
			CFrame = at(col, 1.1, depth),
			Color = Color3.fromRGB(80, 80, 90),
			Material = Enum.Material.Neon,
			CanCollide = false,
			Parent = pad,
		})
		local collect = block({
			Name = "Collect",
			Size = Vector3.new(3.4, 0.4, 3.4),
			CFrame = at(col + 5.6, 1.2, depth),
			Color = Color3.fromRGB(80, 255, 120),
			Material = Enum.Material.Neon,
			Transparency = 1,
			CanCollide = false,
			Parent = pad,
		})
		local storedGui, storedLabel = billboard(collect, UDim2.fromScale(5, 1.6), Vector3.new(0, 2.2, 0), "",
			Color3.fromRGB(120, 255, 140), 45)
		storedGui.Enabled = false
		pads[i] = {
			index = i,
			part = pad,
			glow = glow,
			collect = collect,
			storedGui = storedGui,
			storedLabel = storedLabel,
			top = at(col, 1.6, depth),
		}
	end

	model.PrimaryPart = model:FindFirstChild("Floor") :: BasePart
	model.Parent = world
	WorldBuilder.Plots[index] = {
		index = index,
		model = model,
		cf = cf,
		size = Vector3.new(PLOT_W, 40, PLOT_D),
		center = at(0, 20, PLOT_D / 2),
		barrier = barrier,
		lockButton = lockButton,
		lockLabel = lockLabel,
		signLabel = signLabel,
		pads = pads,
		spawn = CFrame.lookAt(at(0, 4, 8).Position, entrance + Vector3.new(0, 4, 0) - inward * 10),
		outside = at(0, 4, -10),
	}
end

function WorldBuilder.init(_S)
	setupLighting()
	local world = New("Folder", { Name = "World", Parent = workspace })
	buildGround(world)
	buildBelt(world)
	local plotsFolder = New("Folder", { Name = "Plots", Parent = workspace })
	local index = 0
	for _, side in { -1, 1 } do
		for _, z in PLOT_Z do
			index += 1
			buildPlot(plotsFolder, index, Vector3.new(side * ENTRANCE_X, 0, z), Vector3.new(side, 0, 0))
		end
	end
	New("Folder", { Name = "BeltSlimes", Parent = workspace })
	New("Folder", { Name = "BaseSlimes", Parent = workspace })
	print(string.format("[WorldBuilder] built %d plots for %s", #WorldBuilder.Plots, Config.GameName))
end

return WorldBuilder
