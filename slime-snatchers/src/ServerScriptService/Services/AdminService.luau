-- Owner/admin tools for live "admin events" (huge for CCU in steal games):
-- boost luck, start events, spawn rare slimes, announce. Admins are: Studio
-- testers, the experience owner, Config.AdminUserIds, and group ranks >= AdminMinRank.
local RunService = game:GetService("RunService")
local TextService = game:GetService("TextService")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Config = require(Shared:WaitForChild("Config"))
local Rarities = require(Shared:WaitForChild("Rarities"))

local AdminService = {}

local S
local admins: { [Player]: boolean } = {}

local function rankIn(player: Player, groupId: number): number
	local ok, rank = pcall(function()
		return player:GetRankInGroupAsync(groupId)
	end)
	return if ok and type(rank) == "number" then rank else 0
end

local function computeAdmin(player: Player): boolean
	if RunService:IsStudio() or table.find(Config.AdminUserIds, player.UserId) then
		return true
	end
	if game.CreatorType == Enum.CreatorType.User and game.CreatorId == player.UserId then
		return true
	end
	if game.CreatorType == Enum.CreatorType.Group and rankIn(player, game.CreatorId) >= Config.AdminMinRank then
		return true
	end
	return Config.GroupId > 0 and rankIn(player, Config.GroupId) >= Config.AdminMinRank
end

function AdminService.isAdmin(player: Player): boolean
	return admins[player] == true
end

local COMMANDS = {}

function COMMANDS.luck(_player, args)
	S.EventService.setAdminLuck(tonumber(args.mult) or 2, (tonumber(args.minutes) or 5) * 60)
	return true
end

function COMMANDS.event(_player, args)
	return S.EventService.startEvent(tostring(args.key))
end

function COMMANDS.spawn(_player, args)
	local rarity = tostring(args.rarity)
	if not Rarities.ByKey[rarity] then
		return false
	end
	local count = math.clamp(tonumber(args.count) or 1, 1, 10)
	for _ = 1, count do
		S.SlimeService.forceSpawn(rarity)
		task.wait(0.4)
	end
	return true
end

function COMMANDS.rain(player, args)
	S.EventService.addBoost("rain", (tonumber(args.minutes) or 5) * 60, player.DisplayName .. " (admin)")
	return true
end

function COMMANDS.golden(player, args)
	S.EventService.addBoost("golden", (tonumber(args.minutes) or 5) * 60, player.DisplayName .. " (admin)")
	return true
end

function COMMANDS.cash(player, args)
	S.EconomyService.addCash(player, math.clamp(tonumber(args.amount) or 0, 0, 1e18), "AdminCash", "Gameplay")
	return true
end

function COMMANDS.announce(player, args)
	local text = tostring(args.text or "")
	if #text == 0 or #text > 150 then
		return false
	end
	-- all text shown to other players must pass Roblox text filtering
	local ok, filtered = pcall(function()
		local result = TextService:FilterStringAsync(text, player.UserId)
		return result:GetNonChatStringForBroadcastAsync()
	end)
	if not ok then
		return false
	end
	S.PlayerService.notifyAll("📢 " .. filtered, "event")
	return true
end

function COMMANDS.wipe(player)
	S.DataService.wipe(player)
	S.DataService.saveNow(player)
	player:Kick("Your test data was reset. Rejoin to start fresh!")
	return true
end

function AdminService.init(services)
	S = services
	S.PlayerService.onReady(function(player)
		admins[player] = computeAdmin(player)
		player:SetAttribute("Admin", admins[player])
	end)
	S.PlayerService.onLeaving(function(player)
		admins[player] = nil
	end)
	S.PlayerService.addState(function(player)
		return { isAdmin = admins[player] == true, eventKeys = S.EventService.eventKeys() }
	end)
	S.PlayerService.registerAction("admin", function(player, payload)
		if not admins[player] or type(payload) ~= "table" then
			return { ok = false }
		end
		local command = COMMANDS[tostring(payload.cmd)]
		if not command then
			return { ok = false, msg = "Unknown command" }
		end
		local ok = command(player, payload)
		return { ok = ok == true, msg = if ok then "Done!" else "Failed" }
	end)
end

return AdminService
