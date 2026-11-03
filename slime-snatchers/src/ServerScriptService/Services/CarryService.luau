-- Stealing: a thief picks up a slime from an unprotected base, carries it over
-- their head (slowed, highlighted) and must reach their own base. Getting
-- bonked, dying, timing out or leaving returns it to the owner. The slime stays
-- in the victim's data (flag `carried`) until delivery, so nothing is lost if a
-- server shuts down mid-steal.
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Config = require(Shared:WaitForChild("Config"))
local Economy = require(Shared:WaitForChild("Economy"))
local Format = require(Shared:WaitForChild("Format"))
local New = require(Shared:WaitForChild("New"))
local Rarities = require(Shared:WaitForChild("Rarities"))
local SlimeModel = require(Shared:WaitForChild("SlimeModel"))
local Slimes = require(Shared:WaitForChild("Slimes"))

local Tuning = Config.Tuning
local CarryService = {}

local S
local carries: { [Player]: any } = {}

function CarryService.isCarrying(player: Player): boolean
	return carries[player] ~= nil
end

local function rootOf(player: Player): BasePart?
	local character = player.Character
	local root = character and character:FindFirstChild("HumanoidRootPart")
	if root and root:IsA("BasePart") then
		return root
	end
	return nil
end

local function alive(player: Player): boolean
	local character = player.Character
	local humanoid = character and character:FindFirstChildOfClass("Humanoid")
	return humanoid ~= nil and humanoid.Health > 0
end

local function cleanupVisual(carry)
	if carry.model then
		carry.model:Destroy()
	end
	if carry.highlight then
		carry.highlight:Destroy()
	end
end

function CarryService.tryStealing(thief: Player, victim: Player, uid: string)
	if not S.PlayerService.isReady(thief) or not S.PlayerService.isReady(victim) or thief == victim then
		return
	end
	if carries[thief] then
		S.PlayerService.notify(thief, "You're already carrying a slime!", "warning")
		return
	end
	local plot = S.PlotService.get(victim)
	if not plot then
		return
	end
	local protected, reason = S.PlotService.isProtected(plot)
	if protected then
		S.PlayerService.notify(thief, reason or "Protected!", "error")
		return
	end
	local slime = S.SlimeService.find(victim, uid)
	if not slime or slime.carried then
		return
	end
	if slime.shield and slime.shield > os.time() then
		S.PlayerService.notify(thief, "This slime is 🛡️ protected for " .. Format.time(slime.shield - os.time()), "error")
		return
	end
	if not S.SlimeService.freePad(thief) then
		S.PlayerService.notify(thief, "Your base is full! Make room before stealing.", "error")
		return
	end
	local root = rootOf(thief)
	local model = S.SlimeService.getModel(victim, uid)
	if not root or not alive(thief) or not model or not model.PrimaryPart then
		return
	end
	if (root.Position - model.PrimaryPart.Position).Magnitude > 18 then
		return
	end

	slime.carried = thief.UserId
	S.SlimeService.removeModel(victim, uid)
	S.EconomyService.clearPad(victim, slime.pad)
	S.PlotService.setPadOccupied(plot, slime.pad, false)

	local carryModel = SlimeModel.build(slime.id, slime.mut, slime.lvl, "carry")
	carryModel:ScaleTo(0.75 * Economy.levelScale(slime.lvl))
	local body = carryModel.PrimaryPart :: BasePart
	body.Anchored = false
	body.Massless = true
	body.CanCollide = false
	body.CanQuery = false
	carryModel:PivotTo(root.CFrame * CFrame.new(0, 4.6, 0))
	New("WeldConstraint", { Part0 = root, Part1 = body, Parent = body })
	carryModel.Name = "CarriedSlime"
	carryModel.Parent = thief.Character
	local highlight = New("Highlight", {
		FillColor = Color3.fromRGB(255, 50, 70),
		OutlineColor = Color3.fromRGB(255, 255, 255),
		FillTransparency = 0.65,
		DepthMode = Enum.HighlightDepthMode.AlwaysOnTop,
		Adornee = thief.Character,
		Parent = thief.Character,
	})
	carries[thief] = {
		victim = victim,
		uid = uid,
		slime = slime,
		model = carryModel,
		highlight = highlight,
		started = os.clock(),
	}
	S.PlayerService.refreshSpeed(thief)
	local def = Slimes.ById[slime.id]
	S.PlayerService.notify(victim, "🚨 " .. thief.DisplayName .. " is stealing your " .. def.name .. "! BONK them!", "alert")
	S.PlayerService.fx(victim, "stolenAlert", { thief = thief.DisplayName, name = def.name })
	S.PlayerService.notify(thief, "🏃 Run back to YOUR base with " .. def.name .. "!", "info")
	S.PlayerService.sync(thief, { carrying = { id = slime.id, mut = slime.mut, name = def.name } })
end

function CarryService.drop(thief: Player, reason: string)
	local carry = carries[thief]
	if not carry then
		return
	end
	carries[thief] = nil
	cleanupVisual(carry)
	if thief.Parent then
		S.PlayerService.refreshSpeed(thief)
		S.PlayerService.sync(thief, { carrying = false })
		S.PlayerService.notify(thief, "💨 The slime escaped back home! (" .. reason .. ")", "warning")
	end
	local victim = carry.victim
	if victim.Parent and S.PlayerService.isReady(victim) then
		local slime = S.SlimeService.find(victim, carry.uid)
		if slime then
			S.SlimeService.restoreModel(victim, slime)
			S.PlayerService.notify(victim, "✅ Your " .. Slimes.ById[slime.id].name .. " is back!", "success")
		end
	end
end

local function deliver(thief: Player)
	local carry = carries[thief]
	carries[thief] = nil
	cleanupVisual(carry)
	S.PlayerService.refreshSpeed(thief)
	S.PlayerService.sync(thief, { carrying = false })
	local victim = carry.victim
	local removed = S.SlimeService.removeSlime(victim, carry.uid)
	if not removed then
		return
	end
	local granted = S.SlimeService.grantSlime(thief, removed.id, removed.mut, { level = removed.lvl })
	if not granted then
		removed.carried = nil
		S.SlimeService.grantSlime(victim, removed.id, removed.mut, { level = removed.lvl, pad = removed.pad })
		S.PlayerService.notify(thief, "Your base was full — the slime escaped!", "warning")
		return
	end
	local def = Slimes.ById[removed.id]
	local thiefProfile = S.DataService.get(thief)
	local victimProfile = S.DataService.get(victim)
	thiefProfile.data.stats.steals += 1
	S.PlayerService.setStat(thief, "Steals", thiefProfile.data.stats.steals)
	if victimProfile then
		victimProfile.data.stats.stolenFrom += 1
	end
	S.RewardService.progress(thief, "steal", 1)
	S.PlayerService.notify(thief, "😈 You stole " .. def.name .. "!", "success")
	S.PlayerService.fx(thief, "celebrate", { text = "STOLEN!" })
	S.PlayerService.notify(victim, "😭 " .. thief.DisplayName .. " stole your " .. def.name
		.. "! Lock your base 🔒 or get a 🛡️ Shield.", "alert")
	S.PlayerService.fx(victim, "stolen", { name = def.name })
	if Rarities.ByKey[def.rarity].rank >= Rarities.ByKey.Legendary.rank then
		S.PlayerService.notifyAll(string.format("😈 %s stole %s's %s %s!", thief.DisplayName, victim.DisplayName,
			def.rarity, def.name), "event")
	end
end

-- Called when either side leaves or the thief dies.
function CarryService.cancelFor(player: Player, reason: string)
	if carries[player] then
		CarryService.drop(player, reason)
	end
	for thief, carry in carries do
		if carry.victim == player then
			carries[thief] = nil
			cleanupVisual(carry)
			carry.slime.carried = nil
			if thief.Parent then
				S.PlayerService.refreshSpeed(thief)
				S.PlayerService.sync(thief, { carrying = false })
				S.PlayerService.notify(thief, "The owner left — the slime vanished!", "warning")
			end
		end
	end
end

function CarryService.init(services)
	S = services
	S.PlayerService.onLeaving(function(player)
		CarryService.cancelFor(player, "left")
	end)
	S.PlayerService.addState(function()
		return { carrying = false }
	end)
end

function CarryService.start()
	while true do
		task.wait(0.2)
		for thief, carry in carries do
			local root = rootOf(thief)
			if not thief.Parent or not root or not alive(thief) then
				CarryService.drop(thief, "fainted")
			elseif os.clock() - carry.started > Tuning.CarryMaxSeconds then
				CarryService.drop(thief, "too slow")
			else
				local plot = S.PlotService.get(thief)
				if plot and S.PlotService.isInside(plot, root.Position) then
					deliver(thief)
				end
			end
		end
	end
end

return CarryService
