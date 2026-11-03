-- Global leaderboards (OrderedDataStore) rendered on the two plaza boards.
-- OrderedDataStores only hold integers, so lifetime cash is stored as
-- log10(cash) * 1e6, which keeps ordering and 6 digits of precision.
local DataStoreService = game:GetService("DataStoreService")
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Config = require(Shared:WaitForChild("Config"))
local Format = require(Shared:WaitForChild("Format"))
local New = require(Shared:WaitForChild("New"))

local LeaderboardService = {}

local S
local stores = {}
local rows = {} -- [boardName] = { TextLabel }
local names: { [number]: string } = {}

local SUBMIT_EVERY = 300
local REFRESH_EVERY = 180
local ROWS = 10

local function encodeEarned(x: number): number
	return math.floor(math.log10(math.max(x, 1)) * 1e6)
end

local function decodeEarned(v: number): number
	return 10 ^ (v / 1e6)
end

local BOARDS = {
	{ name = "Earned", store = Config.LeaderboardEarnedStore, encode = encodeEarned, decode = decodeEarned,
		format = Format.cash, field = "totalEarned" },
	{ name = "Rebirths", store = Config.LeaderboardRebirthStore, encode = math.floor,
		decode = function(v)
			return v
		end, format = function(v)
			return "⭐ " .. Format.num(v)
		end, field = "rebirths" },
}

local function getStore(board)
	if stores[board.name] == nil and not S.DataService.isMemoryMode() then
		local ok, result = pcall(function()
			return DataStoreService:GetOrderedDataStore(board.store)
		end)
		stores[board.name] = if ok then result else false
	end
	return stores[board.name] or nil
end

local function nameOf(userId: number): string
	if names[userId] then
		return names[userId]
	end
	local ok, name = pcall(function()
		return Players:GetNameFromUserIdAsync(userId)
	end)
	names[userId] = if ok then name else ("User " .. userId)
	return names[userId]
end

function LeaderboardService.submit(player: Player)
	local profile = S.DataService.get(player)
	if not profile then
		return
	end
	-- read values up front: the profile may be released while SetAsync yields
	local values = {}
	for _, board in BOARDS do
		values[board.name] = board.encode(profile.data[board.field])
	end
	for _, board in BOARDS do
		local store = getStore(board)
		local value = values[board.name]
		if store and value > 0 then
			pcall(function()
				store:SetAsync(tostring(player.UserId), value)
			end)
		end
	end
end

local function buildBoard(part: BasePart)
	local gui = New("SurfaceGui", {
		Face = Enum.NormalId.Back,
		SizingMode = Enum.SurfaceGuiSizingMode.PixelsPerStud,
		PixelsPerStud = 30,
		LightInfluence = 0,
		Parent = part,
	})
	local frame = New("Frame", {
		BackgroundColor3 = Color3.fromRGB(30, 25, 50),
		Size = UDim2.fromScale(1, 1),
		Parent = gui,
	}, {
		New("UIPadding", {
			PaddingTop = UDim.new(0.03, 0),
			PaddingLeft = UDim.new(0.05, 0),
			PaddingRight = UDim.new(0.05, 0),
		}),
		New("UIListLayout", { SortOrder = Enum.SortOrder.LayoutOrder, Padding = UDim.new(0.012, 0) }),
	})
	New("TextLabel", {
		BackgroundTransparency = 1,
		Size = UDim2.fromScale(1, 0.1),
		Font = Enum.Font.FredokaOne,
		Text = part:GetAttribute("Title") or part.Name,
		TextColor3 = Color3.fromRGB(255, 220, 90),
		TextScaled = true,
		LayoutOrder = 0,
		Parent = frame,
	})
	local list = {}
	for i = 1, ROWS do
		list[i] = New("TextLabel", {
			BackgroundColor3 = if i % 2 == 0 then Color3.fromRGB(45, 40, 70) else Color3.fromRGB(55, 48, 85),
			Size = UDim2.fromScale(1, 0.074),
			Font = Enum.Font.FredokaOne,
			Text = "",
			TextColor3 = if i == 1 then Color3.fromRGB(255, 215, 0)
				elseif i == 2 then Color3.fromRGB(220, 220, 235)
				elseif i == 3 then Color3.fromRGB(230, 160, 90)
				else Color3.fromRGB(255, 255, 255),
			TextScaled = true,
			TextXAlignment = Enum.TextXAlignment.Left,
			LayoutOrder = i,
			Parent = frame,
		}, { New("UICorner", { CornerRadius = UDim.new(0.2, 0) }) })
	end
	return list
end

local function refresh()
	for _, board in BOARDS do
		local store = getStore(board)
		local labels = rows[board.name]
		if store and labels then
			local ok, pages = pcall(function()
				return store:GetSortedAsync(false, ROWS)
			end)
			if ok then
				local entries = pages:GetCurrentPage()
				for i = 1, ROWS do
					local entry = entries[i]
					if entry then
						local userId = tonumber(entry.key) or 0
						labels[i].Text = string.format("  #%d  %s   %s", i, nameOf(userId), board.format(board.decode(entry.value)))
					else
						labels[i].Text = string.format("  #%d  —", i)
					end
				end
			end
		end
	end
end

function LeaderboardService.init(services)
	S = services
	local folder = workspace:FindFirstChild("World") and workspace.World:FindFirstChild("Leaderboards")
	if folder then
		for _, board in BOARDS do
			local part = folder:FindFirstChild(board.name)
			if part and part:IsA("BasePart") then
				rows[board.name] = buildBoard(part)
			end
		end
	end
	S.PlayerService.onLeaving(function(player)
		task.spawn(LeaderboardService.submit, player)
	end)
end

function LeaderboardService.start()
	task.wait(10)
	refresh()
	local lastSubmit = os.clock()
	while true do
		task.wait(REFRESH_EVERY)
		if os.clock() - lastSubmit >= SUBMIT_EVERY then
			lastSubmit = os.clock()
			for _, player in S.PlayerService.readyPlayers() do
				LeaderboardService.submit(player)
				task.wait(1)
			end
		end
		refresh()
	end
end

return LeaderboardService
