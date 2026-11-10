using Unreal.Core.Attributes;
using Unreal.Core.Contracts;
using Unreal.Core.Models;
using Unreal.Core.Models.Enums;

namespace FortniteReplayReader.Models.NetFieldExports.Items;

/// <summary>
/// ChestCounter patch: loot cannons (interact, they fire loot on the ground). Every cannon blueprint's
/// ClassNetCache is aliased onto this one through NetFieldParser.ClassNetCacheAliasResolver.
/// </summary>
[NetFieldExportClassNetCache(LootCannonCache.Path, minimalParseMode: ParseMode.Minimal)]
public class LootCannonCache
{
    public const string Path = "__ChestCounter.LootCannon_ClassNetCache";

    [NetFieldExportRPC("FireLootCannon", LootCannonFired.Path, isFunction: true)]
    public LootCannonFired? FireLootCannon { get; set; }
}

[NetFieldExportGroup(LootCannonFired.Path, minimalParseMode: ParseMode.Minimal)]
public class LootCannonFired : INetFieldExportGroup
{
    public const string Path = "/LootCannon/Gameplay/BGA_LootCannon.BGA_LootCannon_C:FireLootCannon";

    [NetFieldExport("LootTierToFire", RepLayoutCmdType.Ignore)] public DebuggingObject? LootTierToFire { get; set; }
    [NetFieldExport("InteractingPawn", RepLayoutCmdType.PropertyObject)] public uint? InteractingPawn { get; set; }
}
