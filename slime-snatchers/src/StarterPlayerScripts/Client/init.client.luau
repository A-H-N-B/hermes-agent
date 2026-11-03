-- Client entry point: builds the UI, connects remotes, then asks the server
-- for the full state (retrying until the player's data has loaded).
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local New = require(ReplicatedStorage:WaitForChild("Shared"):WaitForChild("New"))
local Panels = script:WaitForChild("Panels")

local State = require(script:WaitForChild("State"))
local Net = require(script:WaitForChild("Net"))
local UIKit = require(script:WaitForChild("UIKit"))
local Sounds = require(script:WaitForChild("Sounds"))
local Notify = require(script:WaitForChild("Notify"))
local Effects = require(script:WaitForChild("Effects"))
local HUD = require(script:WaitForChild("HUD"))
local World = require(script:WaitForChild("World"))
local ChatTags = require(script:WaitForChild("ChatTags"))
local Tutorial = require(script:WaitForChild("Tutorial"))

local player = Players.LocalPlayer

-- listen first so no pushed state is missed
Net.on("Sync", State.apply)
Net.on("Notify", function(text, kind)
	Notify.show(text, kind)
end)
Net.on("FX", function(name, data)
	Effects.play(name, data)
end)

local gui = New("ScreenGui", {
	Name = "SlimeSnatchersUI",
	ResetOnSpawn = false,
	ZIndexBehavior = Enum.ZIndexBehavior.Sibling,
	Parent = player:WaitForChild("PlayerGui"),
})

UIKit.init(gui)
Sounds.init()
Notify.init(gui)
Effects.init(gui)
for _, name in { "Shop", "Rewards", "Collection", "Progress", "Misc" } do
	require(Panels:WaitForChild(name)).init()
end
HUD.init(gui, UIKit.toggle)
World.init(UIKit.open)
ChatTags.init()
Tutorial.init(gui)

task.spawn(function()
	for _ = 1, 90 do
		local result = Net.call("ready")
		if result.ok and result.state then
			State.apply(result.state)
			return
		end
		task.wait(1)
	end
	Notify.show("Still loading your data… if this takes long, please rejoin.", "warning")
end)
