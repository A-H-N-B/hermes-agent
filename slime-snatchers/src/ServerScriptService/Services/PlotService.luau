-- Base ownership, locks/shields, pads and cash collect plates.
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Config = require(Shared:WaitForChild("Config"))
local Economy = require(Shared:WaitForChild("Economy"))
local Format = require(Shared:WaitForChild("Format"))

local PlotService = {}

local S
local byPlayer: { [Player]: any } = {}

local LOCK_COLOR = Color3.fromRGB(255, 60, 80)
local SHIELD_COLOR = Color3.fromRGB(80, 170, 255)
local PAD_ON = Color3.fromRGB(90, 230, 255)
local PAD_OFF = Color3.fromRGB(70, 70, 80)

local function now(): number
	return workspace:GetServerTimeNow()
end

local function playerFromHit(hit: BasePart): Player?
	local model = hit:FindFirstAncestorOfClass("Model")
	return model and Players:GetPlayerFromCharacter(model)
end

function PlotService.get(player: Player)
	return byPlayer[player]
end

function PlotService.isInside(plot, position: Vector3): boolean
	local p = plot.center:PointToObjectSpace(position)
	return math.abs(p.X) <= plot.size.X / 2 and math.abs(p.Y) <= plot.size.Y / 2 and math.abs(p.Z) <= plot.size.Z / 2
end

function PlotService.shieldLeft(plot): number
	if not plot.owner then
		return 0
	end
	return S.EconomyService.boostLeft(plot.owner, "shield")
end

function PlotService.lockLeft(plot): number
	if S.EventService.locksDisabled() then
		return 0
	end
	return math.max(0, plot.lockedUntil - now())
end

-- Returns true + reason when nobody may steal from this base right now.
function PlotService.isProtected(plot): (boolean, string?)
	if PlotService.shieldLeft(plot) > 0 then
		return true, "This base has a 🛡️ shield!"
	end
	if PlotService.lockLeft(plot) > 0 then
		return true, "This base is 🔒 locked!"
	end
	return false, nil
end

local function setBarrier(plot)
	local shield = PlotService.shieldLeft(plot) > 0
	local locked = PlotService.lockLeft(plot) > 0
	local barrier = plot.barrier :: BasePart
	if shield or locked then
		barrier.Transparency = 0.35
		barrier.CanCollide = true
		barrier.Color = if shield then SHIELD_COLOR else LOCK_COLOR
	else
		barrier.Transparency = 1
		barrier.CanCollide = false
	end
	plot.model:SetAttribute("Protected", shield or locked)
end

function PlotService.refreshPads(player: Player)
	local plot = byPlayer[player]
	local profile = S.DataService.get(player)
	if not plot or not profile then
		return
	end
	local unlocked = PlotService.maxPads(player)
	for i, pad in plot.pads do
		local on = i <= unlocked
		pad.glow.Color = if on then PAD_ON else PAD_OFF
		pad.part.Color = if on then Color3.fromRGB(245, 245, 250) else Color3.fromRGB(150, 150, 160)
	end
end

function PlotService.maxPads(player: Player): number
	local profile = S.DataService.get(player)
	if not profile then
		return Config.Tuning.BasePads
	end
	return Economy.maxPads(profile.data.rebirths, S.MonetizationService.has(player, "ExtraPads"))
end

function PlotService.setPadStored(plot, padIndex: number, amount: number)
	local pad = plot.pads[padIndex]
	if not pad then
		return
	end
	if amount >= 1 then
		pad.storedLabel.Text = Format.cash(amount)
		pad.storedGui.Enabled = true
	else
		pad.storedGui.Enabled = false
	end
end

function PlotService.setPadOccupied(plot, padIndex: number, occupied: boolean)
	local pad = plot.pads[padIndex]
	if pad then
		pad.collect.Transparency = if occupied then 0.1 else 1
		if not occupied then
			pad.storedGui.Enabled = false
		end
	end
end

function PlotService.lock(player: Player)
	local plot = byPlayer[player]
	if not plot then
		return { ok = false }
	end
	if S.EventService.locksDisabled() then
		S.PlayerService.notify(player, "🌙 Locks are down during the Goo Moon!", "warning")
		return { ok = false }
	end
	local t = now()
	if plot.lockedUntil > t then
		return { ok = false }
	end
	if plot.lockCooldownUntil > t then
		S.PlayerService.notify(player, "Lock recharging: " .. Format.time(plot.lockCooldownUntil - t), "warning")
		return { ok = false }
	end
	local duration = Config.Tuning.LockSeconds * (if S.MonetizationService.has(player, "LongLock") then 2 else 1)
	plot.lockedUntil = t + duration
	plot.lockCooldownUntil = plot.lockedUntil + Config.Tuning.LockCooldown
	plot.model:SetAttribute("LockedUntil", plot.lockedUntil)
	setBarrier(plot)
	S.PlayerService.notify(player, "🔒 Base locked for " .. Format.time(duration), "success")
	S.PlayerService.fx(player, "sound", { name = "Click" })
	S.RewardService.tutorialStep(player, 4)
	S.PlayerService.sync(player, PlotService.state(player))
	return { ok = true }
end

function PlotService.unlockAll()
	for _, plot in S.WorldBuilder.Plots do
		setBarrier(plot)
	end
end

function PlotService.onShieldChanged(player: Player)
	local plot = byPlayer[player]
	if plot then
		setBarrier(plot)
	end
end

function PlotService.state(player: Player)
	local plot = byPlayer[player]
	if not plot then
		return {}
	end
	return {
		plotIndex = plot.index,
		lockedUntil = plot.lockedUntil,
		lockCooldownUntil = plot.lockCooldownUntil,
		maxPads = PlotService.maxPads(player),
	}
end

local function assign(player: Player)
	for _, plot in S.WorldBuilder.Plots do
		if not plot.owner then
			plot.owner = player
			plot.lockedUntil = 0
			plot.lockCooldownUntil = 0
			plot.model:SetAttribute("OwnerId", player.UserId)
			plot.model:SetAttribute("LockedUntil", 0)
			plot.signLabel.Text = player.DisplayName .. "'s Base"
			player:SetAttribute("PlotIndex", plot.index)
			byPlayer[player] = plot
			PlotService.refreshPads(player)
			setBarrier(plot)
			return plot
		end
	end
	return nil
end

local function free(player: Player)
	local plot = byPlayer[player]
	if not plot then
		return
	end
	byPlayer[player] = nil
	plot.owner = nil
	plot.lockedUntil = 0
	plot.model:SetAttribute("OwnerId", 0)
	plot.signLabel.Text = "Empty Base"
	for i in plot.pads do
		PlotService.setPadOccupied(plot, i, false)
		plot.pads[i].glow.Color = PAD_OFF
	end
	setBarrier(plot)
	plot.lockLabel.Text = "🔒 LOCK BASE"
end

local function ejectIntruders(plot)
	for _, other in Players:GetPlayers() do
		if other ~= plot.owner then
			local character = other.Character
			local root = character and character:FindFirstChild("HumanoidRootPart")
			if root and root:IsA("BasePart") and PlotService.isInside(plot, root.Position) then
				character:PivotTo(plot.outside)
			end
		end
	end
end

local function hookPlot(plot)
	local lastTouch = {}
	for i, pad in plot.pads do
		pad.collect.Touched:Connect(function(hit)
			if not plot.owner then
				return
			end
			local player = playerFromHit(hit)
			if player ~= plot.owner then
				return
			end
			local t = os.clock()
			if (lastTouch[i] or 0) + 0.25 > t then
				return
			end
			lastTouch[i] = t
			S.EconomyService.collectPad(player, i)
		end)
	end
	plot.lockButton.Touched:Connect(function(hit)
		local player = playerFromHit(hit)
		if player and player == plot.owner and plot.lockedUntil <= now() then
			PlotService.lock(player)
		end
	end)
end

function PlotService.init(services)
	S = services
	for _, plot in S.WorldBuilder.Plots do
		plot.lockedUntil = 0
		plot.lockCooldownUntil = 0
		hookPlot(plot)
	end
	S.PlayerService.onReady(function(player)
		if not assign(player) then
			player:Kick("This server is full. Please join another server!")
		end
	end)
	S.PlayerService.onLeaving(free)
	S.PlayerService.registerAction("lock", function(player)
		return PlotService.lock(player)
	end)
	S.PlayerService.addState(function(player)
		return PlotService.state(player)
	end)
end

function PlotService.start()
	while true do
		task.wait(0.5)
		local t = now()
		for _, plot in S.WorldBuilder.Plots do
			if plot.owner then
				local protected = PlotService.isProtected(plot)
				setBarrier(plot)
				if protected then
					ejectIntruders(plot)
				end
				local shield = PlotService.shieldLeft(plot)
				local lock = PlotService.lockLeft(plot)
				if shield > 0 then
					plot.lockLabel.Text = "🛡️ SHIELD " .. Format.time(shield)
				elseif lock > 0 then
					plot.lockLabel.Text = "🔒 LOCKED " .. Format.time(lock)
				elseif S.EventService.locksDisabled() then
					plot.lockLabel.Text = "🌙 LOCKS DOWN"
				elseif plot.lockCooldownUntil > t then
					plot.lockLabel.Text = "⏳ " .. Format.time(plot.lockCooldownUntil - t)
				else
					plot.lockLabel.Text = "🔓 STEP TO LOCK"
				end
			end
		end
	end
end

return PlotService
