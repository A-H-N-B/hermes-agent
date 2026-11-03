--[[
	Slime Snatchers — CONFIG (file yang perlu kamu edit)

	1. Buat Game Pass & Developer Product di Creator Hub
	   (create.roblox.com > Creations > [game kamu] > Monetization).
	2. Tempel ID-nya di bawah (ganti angka 0). Item dengan id = 0 otomatis
	   disembunyikan dari toko, jadi game tetap jalan walau belum semua dibuat.
	3. `price` di sini hanya cadangan tampilan; harga asli diambil otomatis
	   dari Roblox (GetProductInfoAsync), jadi samakan saja dengan harga di Creator Hub.
]]

local Config = {}

Config.GameName = "Slime Snatchers"
Config.Version = "1.0.0"

-- Ganti nama ini hanya jika ingin "reset" semua data pemain.
Config.DataStoreName = "SlimeSnatchers_Data_v1"
Config.GiftStoreName = "SlimeSnatchers_Gifts_v1"
Config.LeaderboardEarnedStore = "SlimeSnatchers_LB_Earned_v1"
Config.LeaderboardRebirthStore = "SlimeSnatchers_LB_Rebirths_v1"

-- Admin: UserId kamu otomatis admin jika game dimiliki akunmu. Tambah teman di sini.
Config.AdminUserIds = {}
-- Grup Roblox (0 = mati). Anggota grup dapat +10% cash dan admin = rank >= AdminMinRank.
Config.GroupId = 0
Config.GroupBonus = 0.10
Config.AdminMinRank = 254

-- Link sosial yang tampil di layar Codes (teks saja, Roblox melarang link klik).
Config.SocialText = "Join our group for +10% cash & codes!"

---------------------------------------------------------------------------
-- GAME PASSES (sekali beli, permanen)
---------------------------------------------------------------------------
Config.Passes = {
	DoubleCash = {
		id = 0,
		price = 399,
		name = "2x Cash",
		icon = "💰",
		desc = "Earn 2x cash from every slime, forever.",
		order = 1,
	},
	VIP = {
		id = 0,
		price = 299,
		name = "VIP",
		icon = "👑",
		desc = "+50% cash, golden VIP tag, 6h offline earnings.",
		order = 2,
	},
	ExtraPads = {
		id = 0,
		price = 249,
		name = "+6 Slime Pads",
		icon = "🧱",
		desc = "6 extra pads in your base for more slimes.",
		order = 3,
	},
	FastSteal = {
		id = 0,
		price = 199,
		name = "Master Thief",
		icon = "🥷",
		desc = "Steal 2x faster and run at full speed while carrying.",
		order = 4,
	},
	AutoCollect = {
		id = 0,
		price = 149,
		name = "Auto Collect",
		icon = "🧲",
		desc = "Cash goes straight to your wallet. No walking!",
		order = 5,
	},
	LongLock = {
		id = 0,
		price = 149,
		name = "Mega Lock",
		icon = "🔒",
		desc = "Your base lock lasts 2x longer.",
		order = 6,
	},
	Speed = {
		id = 0,
		price = 99,
		name = "Turbo Shoes",
		icon = "👟",
		desc = "+25% walk speed.",
		order = 7,
	},
}

---------------------------------------------------------------------------
-- DEVELOPER PRODUCTS (bisa dibeli berulang)
---------------------------------------------------------------------------
Config.Products = {
	-- Paket cash: jumlahnya ikut naik sesuai income pemain (minutes x income/detik),
	-- jadi tetap berharga di akhir game. Jumlah pasti ditampilkan sebelum membeli.
	CashSmall = { id = 0, price = 25, name = "Cash Pouch", icon = "💵", minutes = 15, min = 1000, order = 1 },
	CashMedium = { id = 0, price = 99, name = "Cash Bag", icon = "💰", minutes = 75, min = 12000, order = 2 },
	CashLarge = { id = 0, price = 299, name = "Cash Vault", icon = "🏦", minutes = 300, min = 150000, order = 3 },
	CashMega = { id = 0, price = 799, name = "Cash Mountain", icon = "⛰️", minutes = 1000, min = 2000000, order = 4 },

	-- Boost untuk SELURUH server (pembeli diumumkan ke semua pemain).
	ServerLuck = { id = 0, price = 99, name = "2x Server Luck", icon = "🍀", minutes = 15, order = 5 },
	ServerRain = { id = 0, price = 79, name = "Slime Rain", icon = "🌧️", minutes = 10, order = 6 },

	-- Boost pribadi.
	CashBoost = { id = 0, price = 49, name = "2x Cash Potion", icon = "🧪", minutes = 30, order = 7 },
	Shield = { id = 0, price = 39, name = "Base Shield", icon = "🛡️", minutes = 10, order = 8 },

	-- Penawaran sekali per akun (jika terbeli lagi, diganti cash setara).
	StarterPack = { id = 0, price = 99, name = "Starter Pack", icon = "🎁", order = 9 },

	-- Gandakan offline earnings saat pemain kembali.
	OfflineDouble = { id = 0, price = 19, name = "2x Offline Cash", icon = "🌙", order = 10 },

	-- Beli langsung slime yang terlihat di conveyor (bukan acak: pemain melihat slime-nya).
	InstantEpic = { id = 0, price = 49, name = "Instant Buy (Epic)", rarity = "Epic", order = 20 },
	InstantLegendary = { id = 0, price = 99, name = "Instant Buy (Legendary)", rarity = "Legendary", order = 21 },
	InstantMythic = { id = 0, price = 199, name = "Instant Buy (Mythic)", rarity = "Mythic", order = 22 },
	InstantCelestial = { id = 0, price = 399, name = "Instant Buy (Celestial)", rarity = "Celestial", order = 23 },
	InstantSecret = { id = 0, price = 799, name = "Instant Buy (Secret)", rarity = "Secret", order = 24 },

	-- Hadiah untuk teman (penerima mendapat efek game pass yang sama, walau sedang offline).
	GiftDoubleCash = { id = 0, price = 399, name = "Gift: 2x Cash", icon = "🎁", gift = "DoubleCash", order = 30 },
	GiftVIP = { id = 0, price = 299, name = "Gift: VIP", icon = "🎁", gift = "VIP", order = 31 },
	GiftAutoCollect = { id = 0, price = 149, name = "Gift: Auto Collect", icon = "🎁", gift = "AutoCollect", order = 32 },

	-- Khusus hadiah Rewarded Video Ads (AdService). Harga bebas, misal 5.
	AdReward = { id = 0, price = 5, name = "Free Reward", icon = "📺", hidden = true, order = 99 },
}

-- Experience Subscription (opsional), contoh "EXP-1234567890". Kosong = mati.
Config.SubscriptionId = ""
Config.SubscriptionName = "Slime Club"
Config.SubscriptionBonus = 0.30

-- Bonus untuk member Roblox Premium / Plus.
Config.PremiumBonus = 0.10

Config.RewardedAds = {
	Enabled = true,
	BoostMinutes = 10, -- hadiah: 2x cash 10 menit
	CooldownSeconds = 240,
}

---------------------------------------------------------------------------
-- CODES (huruf besar/kecil tidak berpengaruh). expires = os.time() UTC, nil = selamanya
---------------------------------------------------------------------------
Config.Codes = {
	RELEASE = { cashMinutes = 10, minCash = 2500, boostMinutes = 10 },
	SLIMY = { cashMinutes = 5, minCash = 1000 },
	SNATCH = { boostMinutes = 15 },
	-- LIKES1K = { cashMinutes = 30, minCash = 10000, expires = 1798761600 },
}

---------------------------------------------------------------------------
-- TUNING (keseimbangan game)
---------------------------------------------------------------------------
Config.Tuning = {
	StartingCash = 100,
	WalkSpeed = 20,
	SpeedPassMult = 1.25,

	BasePads = 10,
	PadsPerRebirth = 2,
	MaxRebirthPads = 14,
	ExtraPadsPass = 6,

	SpawnInterval = 2.3, -- detik antar slime di conveyor
	BeltTravelTime = 42, -- detik dari pipa ke ujung
	PurchaseWalkTime = 1.6,

	LockSeconds = 60,
	LockCooldown = 10,

	StealHoldSeconds = 1.4,
	CarrySpeedMult = 0.72,
	CarryMaxSeconds = 50,
	RobuxSlimeShieldMinutes = 10, -- slime yang dibeli pakai Robux tidak bisa dicuri selama ini

	BonkCooldown = 0.9,
	BonkRange = 9,
	BonkStunSeconds = 1.3,

	OfflineCapHours = 2,
	OfflineCapHoursVIP = 6,
	OfflineCapHoursClub = 8,
	OfflineEfficiency = 0.30,

	FriendBonusEach = 0.05,
	FriendBonusMax = 0.20,
	IndexBonusEach = 0.01, -- +1% cash per jenis slime yang ditemukan

	RebirthCashMultEach = 0.5,
	EventMinGap = 420,
	EventMaxGap = 720,
	EventDuration = 120,

	SellRatio = 0.5,
	AutosaveSeconds = 120,
}

---------------------------------------------------------------------------
-- SOUNDS (ganti dengan SoundId dari Creator Store jika mau)
---------------------------------------------------------------------------
Config.Sounds = {
	Click = "rbxasset://sounds/electronicpingshort.wav",
	Cash = "rbxasset://sounds/electronicpingshort.wav",
	Buy = "rbxasset://sounds/electronicpingshort.wav",
	Bonk = "rbxasset://sounds/swordslash.wav",
	Alert = "rbxasset://sounds/electronicpingshort.wav",
	LevelUp = "rbxasset://sounds/electronicpingshort.wav",
	Music = "", -- contoh "rbxassetid://1234567890" (pastikan audio boleh dipakai)
}

return Config
