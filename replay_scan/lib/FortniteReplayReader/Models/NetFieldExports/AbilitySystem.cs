using Unreal.Core.Attributes;
using Unreal.Core.Contracts;
using Unreal.Core.Models;
using Unreal.Core.Models.Enums;

namespace FortniteReplayReader.Models.NetFieldExports;

/// <summary>
/// ChestCounter patch: granted gameplay abilities (FastArray "ActivatableAbilities") of the recording player.
/// Treasure hunt prizes grant a "GA_TreasureHandler_*" ability, which tells exactly when a treasure was obtained.
/// </summary>
[NetFieldExportClassNetCache("FortAbilitySystemComponentAthena_ClassNetCache", minimalParseMode: ParseMode.Minimal)]
public class AbilitySystemCache
{
    [NetFieldExportRPC("ActivatableAbilities", "/Script/FortniteGame.FortAbilitySystemComponentAthena", enablePropertyChecksum: false)]
    public GrantedAbility? ActivatableAbilities { get; set; }
}

[NetFieldExportGroup("/Script/FortniteGame.FortAbilitySystemComponentAthena", minimalParseMode: ParseMode.Minimal)]
public class GrantedAbility : INetFieldExportGroup
{
    [NetFieldExport("OwnerActor", RepLayoutCmdType.PropertyObject)] public uint? OwnerActor { get; set; }
    [NetFieldExport("AvatarActor", RepLayoutCmdType.PropertyObject)] public uint? AvatarActor { get; set; }
    [NetFieldExport("ability", RepLayoutCmdType.PropertyObject)] public uint? Ability { get; set; }
    [NetFieldExport("SourceObject", RepLayoutCmdType.PropertyObject)] public uint? SourceObject { get; set; }
    [NetFieldExport("InputID", RepLayoutCmdType.PropertyInt)] public int? InputID { get; set; }
}
