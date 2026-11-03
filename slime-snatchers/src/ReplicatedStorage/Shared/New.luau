-- Tiny declarative instance builder used by server and client:
--   New("Frame", { Size = UDim2.fromScale(1, 1), Parent = gui }, { New("UICorner") })
-- Functions given for event names are connected; Parent is applied last so the
-- instance is fully configured before it replicates / renders.
local function New(className: string, props: { [string]: any }?, children: { Instance }?): any
	local inst = Instance.new(className)
	local parent = nil
	if props then
		for key, value in props do
			if key == "Parent" then
				parent = value
			elseif type(value) == "function" then
				(inst :: any)[key]:Connect(value)
			else
				(inst :: any)[key] = value
			end
		end
	end
	if children then
		for _, child in children do
			child.Parent = inst
		end
	end
	if parent then
		inst.Parent = parent
	end
	return inst
end

return New
