-- Visual language: bubbly, high-contrast, readable on small phones.
local Theme = {}

Theme.Font = Enum.Font.FredokaOne
Theme.FontBody = Enum.Font.GothamBold

Theme.Colors = {
	Panel = Color3.fromRGB(42, 34, 74),
	PanelLight = Color3.fromRGB(62, 52, 104),
	Card = Color3.fromRGB(74, 62, 122),
	Stroke = Color3.fromRGB(20, 14, 38),
	Text = Color3.fromRGB(255, 255, 255),
	SubText = Color3.fromRGB(205, 195, 240),
	Green = Color3.fromRGB(70, 220, 110),
	Blue = Color3.fromRGB(70, 160, 255),
	Pink = Color3.fromRGB(255, 95, 170),
	Gold = Color3.fromRGB(255, 200, 50),
	Red = Color3.fromRGB(255, 75, 90),
	Purple = Color3.fromRGB(165, 95, 255),
	Gray = Color3.fromRGB(120, 115, 140),
	Robux = Color3.fromRGB(0, 176, 111),
	Cash = Color3.fromRGB(120, 255, 140),
}

-- Reference resolution the UI is laid out for; UIScale adapts it to the screen.
Theme.Reference = Vector2.new(1100, 640)

return Theme
