-- UI/world sounds from Config.Sounds, respecting the player's settings.
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local SoundService = game:GetService("SoundService")

local Config = require(ReplicatedStorage:WaitForChild("Shared"):WaitForChild("Config"))
local State = require(script.Parent:WaitForChild("State"))

local Sounds = {}

local cache: { [string]: Sound } = {}
local music: Sound? = nil

local VOLUME = { Click = 0.35, Cash = 0.4, Buy = 0.5, Bonk = 0.6, Alert = 0.7, LevelUp = 0.5 }
local PITCH = { Click = 1.4, Cash = 1.8, Buy = 1.1, Bonk = 1, Alert = 0.7, LevelUp = 1.25 }

local function enabled(key: string): boolean
	local settings = State.get("settings")
	return settings == nil or settings[key] ~= false
end

function Sounds.play(name: string)
	if not enabled("sfx") then
		return
	end
	local id = Config.Sounds[name]
	if not id or id == "" then
		return
	end
	local sound = cache[name]
	if not sound then
		sound = Instance.new("Sound")
		sound.Name = name
		sound.SoundId = id
		sound.Volume = VOLUME[name] or 0.5
		sound.PlaybackSpeed = PITCH[name] or 1
		sound.Parent = SoundService
		cache[name] = sound
	end
	SoundService:PlayLocalSound(sound)
end

function Sounds.init()
	if Config.Sounds.Music ~= "" then
		music = Instance.new("Sound")
		music.Name = "Music"
		music.SoundId = Config.Sounds.Music
		music.Looped = true
		music.Volume = 0.25
		music.Parent = SoundService
	end
	State.on("settings", function(settings)
		if music then
			if settings.music ~= false then
				if not music.IsPlaying then
					music:Play()
				end
			else
				music:Stop()
			end
		end
	end)
end

return Sounds
