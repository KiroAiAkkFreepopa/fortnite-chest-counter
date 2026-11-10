using Unreal.Core.Attributes;
using Unreal.Core.Contracts;
using Unreal.Core.Models;
using Unreal.Core.Models.Enums;

namespace FortniteReplayReader.Models.NetFieldExports.Items;

/// <summary>
/// ChestCounter patch: golden "treasure" chest animation actor spawned by the treasure hunt when a chest pays out.
/// </summary>
[NetFieldExportGroup("/DelilahTreasureHuntGameplay/Gameplay/Actors/BGA_DelilahTreasureHuntAnimatedChest.BGA_DelilahTreasureHuntAnimatedChest_C", minimalParseMode: ParseMode.Minimal)]
public class TreasureHuntChest : INetFieldExportGroup
{
    [NetFieldExport("Instigator", RepLayoutCmdType.PropertyObject)] public uint? Instigator { get; set; }
    [NetFieldExport("OwnerPersistentID", RepLayoutCmdType.Ignore)] public DebuggingObject? OwnerPersistentID { get; set; }
    [NetFieldExport("SourceContainer", RepLayoutCmdType.PropertyObject)] public uint? SourceContainer { get; set; }
    [NetFieldExport("OpeningPawn", RepLayoutCmdType.PropertyObject)] public uint? OpeningPawn { get; set; }
    [NetFieldExport("bSuccess", RepLayoutCmdType.PropertyBool)] public bool? bSuccess { get; set; }
    [NetFieldExport("PrizeTemplateId", RepLayoutCmdType.Property)] public DebuggingObject? PrizeTemplateId { get; set; }
    [NetFieldExport("Tier", RepLayoutCmdType.Property)] public DebuggingObject? Tier { get; set; }
    [NetFieldExport("TierData", RepLayoutCmdType.PropertyObject)] public uint? TierData { get; set; }
}
