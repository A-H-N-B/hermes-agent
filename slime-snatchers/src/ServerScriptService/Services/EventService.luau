-- Server-wide boosts and timed events. State is mirrored as attributes on
-- ReplicatedStorage.ServerState so every client can show banners/countdowns.
-- Times use workspace:GetServerTimeNow() so client countdowns match the server.
local Lighting = game:GetService("Lighting")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local TweenService = game:GetService("TweenService")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Config = require(Shared:WaitForChild("Config"))
local Format = require(Shared:WaitForChild("Format"))

local EventService = {}

local S
local folder: Folder
local rng = Random.new()

-- purchased / admin boosts: ends = server time, mult = strength
local boosts = {
	luck = { ends = 0, mult = 2 },
	rain = { ends = 0, mult = 2 },
	golden = { ends = 0, mult = 3 },
}
local adminLuck = { ends = 0, mult = 1 }
local current = nil -- natural event definition while active
local currentEnds = 0

local EVENTS = {
	{ key = "LuckyHour", name = "🍀 LUCKY HOUR", desc = "2x luck for everyone!", luck = 2 },
	{ key = "GoldenHour", name = "✨ GOLDEN HOUR", desc = "3x mutation chance!", golden = 3 },
	{ key = "SlimeRain", name = "🌧️ SLIME RAIN", desc = "Slimes spawn 2x faster!", rain = 2 },
	{ key = "GooMoon", name = "🌙 GOO MOON", desc = "All base locks are DOWN! Defend your slimes!", noLocks = true, duration = 60 },
}

local function now(): number
	return workspace:GetServerTimeNow()
end

local function active(b): boolean
	return b.ends > now()
end

local function publish()
	folder:SetAttribute("LuckMult", EventService.luck())
	folder:SetAttribute("LuckEnds", math.max(boosts.luck.ends, adminLuck.ends))
	folder:SetAttribute("RainEnds", boosts.rain.ends)
	folder:SetAttribute("GoldenEnds", boosts.golden.ends)
	folder:SetAttribute("MutationMult", EventService.mutationMult())
	folder:SetAttribute("SpawnMult", EventService.spawnRateMult())
	folder:SetAttribute("EventKey", if current then current.key else "")
	folder:SetAttribute("EventName", if current then current.name else "")
	folder:SetAttribute("EventDesc", if current then current.desc else "")
	folder:SetAttribute("EventEnds", currentEnds)
	folder:SetAttribute("LocksDisabled", EventService.locksDisabled())
end

function EventService.luck(): number
	local luck = 1
	if active(boosts.luck) then
		luck *= boosts.luck.mult
	end
	if active(adminLuck) then
		luck *= adminLuck.mult
	end
	if current and current.luck and currentEnds > now() then
		luck *= current.luck
	end
	return math.min(luck, 100)
end

function EventService.mutationMult(): number
	local mult = 1
	if active(boosts.golden) then
		mult *= boosts.golden.mult
	end
	if current and current.golden and currentEnds > now() then
		mult *= current.golden
	end
	return mult
end

function EventService.spawnRateMult(): number
	local mult = 1
	if active(boosts.rain) then
		mult *= boosts.rain.mult
	end
	if current and current.rain and currentEnds > now() then
		mult *= current.rain
	end
	return mult
end

function EventService.locksDisabled(): boolean
	return current ~= nil and current.noLocks == true and currentEnds > now()
end

-- kind: "luck" | "rain" | "golden". Stacks duration; buyerName is announced.
function EventService.addBoost(kind: string, seconds: number, buyerName: string?)
	local b = boosts[kind]
	assert(b, "unknown boost " .. kind)
	b.ends = math.max(b.ends, now()) + seconds
	publish()
	local labels = { luck = "🍀 2x SERVER LUCK", rain = "🌧️ SLIME RAIN", golden = "✨ GOLDEN SLIMES" }
	if buyerName then
		S.PlayerService.notifyAll(string.format("%s activated %s for everyone! (%s) 💚", buyerName, labels[kind],
			Format.time(b.ends - now())), "event")
		S.PlayerService.fxAll("celebrate", { text = labels[kind] })
	end
	task.delay(seconds + 0.5, publish)
end

function EventService.setAdminLuck(mult: number, seconds: number)
	adminLuck.mult = math.clamp(mult, 1, 100)
	adminLuck.ends = now() + seconds
	publish()
	S.PlayerService.notifyAll(string.format("🛠️ ADMIN EVENT: %dx LUCK for %s!", adminLuck.mult, Format.time(seconds)), "event")
	S.PlayerService.fxAll("celebrate", { text = string.format("%dx LUCK!", adminLuck.mult) })
	task.delay(seconds + 0.5, publish)
end

local function tweenSky(night: boolean)
	local info = TweenInfo.new(3, Enum.EasingStyle.Sine)
	TweenService:Create(Lighting, info, { ClockTime = if night then 0.3 else 14.5 }):Play()
	local grade = Lighting:FindFirstChild("Grade")
	if grade and grade:IsA("ColorCorrectionEffect") then
		TweenService:Create(grade, info, {
			TintColor = if night then Color3.fromRGB(200, 170, 255) else Color3.fromRGB(255, 255, 255),
		}):Play()
	end
end

function EventService.startEvent(key: string, seconds: number?)
	local def = nil
	for _, e in EVENTS do
		if e.key == key then
			def = e
		end
	end
	if not def then
		return false
	end
	local duration = seconds or def.duration or Config.Tuning.EventDuration
	current = def
	currentEnds = now() + duration
	publish()
	if def.noLocks then
		tweenSky(true)
		S.PlotService.unlockAll()
	end
	S.PlayerService.notifyAll(def.name .. " — " .. def.desc, "event")
	S.PlayerService.fxAll("event", { name = def.name, desc = def.desc })
	local token = currentEnds
	task.delay(duration, function()
		if currentEnds == token then
			if def.noLocks then
				tweenSky(false)
			end
			current = nil
			currentEnds = 0
			publish()
			S.PlayerService.notifyAll(def.name .. " has ended.", "info")
		end
	end)
	return true
end

function EventService.eventKeys(): { string }
	local keys = {}
	for _, e in EVENTS do
		table.insert(keys, e.key)
	end
	return keys
end

function EventService.init(services)
	S = services
	folder = Instance.new("Folder")
	folder.Name = "ServerState"
	folder.Parent = ReplicatedStorage
	publish()
end

function EventService.start()
	while true do
		task.wait(rng:NextInteger(Config.Tuning.EventMinGap, Config.Tuning.EventMaxGap))
		if not current and #S.PlayerService.readyPlayers() > 0 then
			EventService.startEvent(EVENTS[rng:NextInteger(1, #EVENTS)].key)
		end
	end
end

return EventService
