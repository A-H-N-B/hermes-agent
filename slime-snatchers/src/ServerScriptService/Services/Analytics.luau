-- Thin wrapper over AnalyticsService (Creator Hub > Analytics). Every call is
-- protected so analytics can never break gameplay.
local AnalyticsService = game:GetService("AnalyticsService")

local Analytics = {}

local function safe(fn, ...)
	local ok, err = pcall(fn, ...)
	if not ok then
		warn("[Analytics] " .. tostring(err))
	end
end

-- flow: "Source" (cash in) | "Sink" (cash out)
-- transactionType: an Enum.AnalyticsEconomyTransactionType name, e.g. "Shop", "IAP", "Gameplay"
function Analytics.economy(player: Player, flow: string, amount: number, balance: number, transactionType: string, sku: string)
	if amount <= 0 or amount ~= amount or amount == math.huge then
		return
	end
	safe(function()
		AnalyticsService:LogEconomyEvent(
			player,
			(Enum.AnalyticsEconomyFlowType :: any)[flow],
			"Cash",
			math.floor(math.min(amount, 1e15)),
			math.floor(math.min(balance, 1e15)),
			transactionType,
			sku
		)
	end)
end

function Analytics.onboarding(player: Player, step: number, stepName: string)
	safe(function()
		AnalyticsService:LogOnboardingFunnelStepEvent(player, step, stepName)
	end)
end

function Analytics.funnel(player: Player, funnel: string, sessionId: string, step: number, stepName: string)
	safe(function()
		AnalyticsService:LogFunnelStepEvent(player, funnel, sessionId, step, stepName)
	end)
end

function Analytics.progression(player: Player, path: string, level: number, levelName: string)
	safe(function()
		AnalyticsService:LogProgressionCompleteEvent(player, path, level, levelName)
	end)
end

function Analytics.custom(player: Player, eventName: string, value: number?)
	safe(function()
		AnalyticsService:LogCustomEvent(player, eventName, value or 1)
	end)
end

return Analytics
