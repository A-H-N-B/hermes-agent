-- Client-side mirror of the player's state. The server pushes partial tables
-- (Sync remote); UI modules subscribe to the keys they render.
local State = {}

State.data = {}
local listeners: { [string]: { (any) -> () } } = {}

function State.get(key: string): any
	return State.data[key]
end

function State.on(key: string, fn: (any) -> ())
	listeners[key] = listeners[key] or {}
	table.insert(listeners[key], fn)
	if key ~= "*" and State.data[key] ~= nil then
		task.spawn(fn, State.data[key])
	end
end

function State.apply(partial)
	if type(partial) ~= "table" then
		return
	end
	for key, value in partial do
		State.data[key] = value
	end
	for key, value in partial do
		local list = listeners[key]
		if list then
			for _, fn in list do
				task.spawn(fn, value)
			end
		end
	end
	local any = listeners["*"]
	if any then
		for _, fn in any do
			task.spawn(fn, partial)
		end
	end
end

-- Convenience: does the player own a pass (bought or gifted)?
function State.has(passKey: string): boolean
	local owned = State.data.owned
	local gifted = State.data.gifted
	return (owned ~= nil and owned[passKey] == true) or (gifted ~= nil and gifted[passKey] == true)
end

function State.passInfo(passKey: string)
	for _, pass in State.data.passes or {} do
		if pass.key == passKey then
			return pass
		end
	end
	return nil
end

function State.productInfo(productKey: string)
	for _, product in State.data.products or {} do
		if product.key == productKey then
			return product
		end
	end
	return nil
end

return State
