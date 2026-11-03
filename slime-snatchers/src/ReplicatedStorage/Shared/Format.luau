-- Number / time formatting shared by server billboards and client UI.
local Format = {}

local SUFFIXES = {
	"", "K", "M", "B", "T", "Qa", "Qi", "Sx", "Sp", "Oc", "No", "Dc",
	"Ud", "Dd", "Td", "Qad", "Qid", "Sxd", "Spd", "Ocd", "Nod", "Vg",
}

local function trim(text: string): string
	if string.find(text, "%.") then
		text = string.gsub(text, "0+$", "")
		text = string.gsub(text, "%.$", "")
	end
	return text
end

-- 3 significant digits: 999, 1.23K, 12.3K, 123K, 1.23M ...
function Format.num(n: number): string
	if n ~= n then
		return "0"
	end
	local negative = n < 0
	n = math.abs(n)
	local text
	if n < 1000 then
		text = tostring(math.floor(n))
	else
		local tier = math.floor(math.log10(n) / 3)
		if tier >= #SUFFIXES then
			text = string.format("%.2e", n)
		else
			local value = n / 10 ^ (tier * 3)
			-- guard against 999.95K rounding up to "1000K"
			if value >= 999.5 and tier + 1 < #SUFFIXES then
				tier += 1
				value = n / 10 ^ (tier * 3)
			end
			local decimals = if value < 10 then 2 elseif value < 100 then 1 else 0
			text = trim(string.format("%." .. decimals .. "f", value)) .. SUFFIXES[tier + 1]
		end
	end
	return if negative then "-" .. text else text
end

function Format.cash(n: number): string
	return "$" .. Format.num(n)
end

function Format.rate(n: number): string
	return "$" .. Format.num(n) .. "/s"
end

-- 75 -> "1:15", 3725 -> "1:02:05"
function Format.time(seconds: number): string
	seconds = math.max(0, math.floor(seconds))
	local h = seconds // 3600
	local m = (seconds % 3600) // 60
	local s = seconds % 60
	if h > 0 then
		return string.format("%d:%02d:%02d", h, m, s)
	end
	return string.format("%d:%02d", m, s)
end

-- Odds as a readable percentage with enough precision for tiny values.
function Format.percent(p: number): string
	local pct = p * 100
	if pct >= 10 then
		return trim(string.format("%.1f", pct)) .. "%"
	elseif pct >= 1 then
		return trim(string.format("%.2f", pct)) .. "%"
	elseif pct >= 0.01 then
		return trim(string.format("%.3f", pct)) .. "%"
	end
	return trim(string.format("%.5f", pct)) .. "%"
end

function Format.mult(m: number): string
	return "x" .. trim(string.format("%.2f", m))
end

return Format
