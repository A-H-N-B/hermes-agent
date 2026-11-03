-- Chat tags ([DEV], [VIP], [Club]) via TextChatService. Visible status is a
-- big reason players buy VIP, so it is shown wherever their name appears.
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local TextChatService = game:GetService("TextChatService")

local Config = require(ReplicatedStorage:WaitForChild("Shared"):WaitForChild("Config"))

local ChatTags = {}

function ChatTags.init()
	TextChatService.OnIncomingMessage = function(message: TextChatMessage)
		local props = Instance.new("TextChatMessageProperties")
		local source = message.TextSource
		if not source then
			return props
		end
		local sender = Players:GetPlayerByUserId(source.UserId)
		if not sender then
			return props
		end
		local tags = {}
		if sender:GetAttribute("Admin") then
			table.insert(tags, '<font color="#FF5A5A">[DEV]</font>')
		end
		if sender:GetAttribute("VIP") then
			table.insert(tags, '<font color="#FFD54A">[VIP]</font>')
		end
		if sender:GetAttribute("Club") then
			table.insert(tags, '<font color="#B48CFF">[' .. Config.SubscriptionName .. "]</font>")
		end
		if #tags > 0 then
			props.PrefixText = table.concat(tags, " ") .. " " .. message.PrefixText
		end
		return props
	end
end

return ChatTags
