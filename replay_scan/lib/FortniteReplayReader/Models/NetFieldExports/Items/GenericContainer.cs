using Unreal.Core.Attributes;
using Unreal.Core.Models.Enums;

namespace FortniteReplayReader.Models.NetFieldExports.Items;

/// <summary>
/// ChestCounter patch: generic searchable container. Unknown chest / container blueprints are
/// aliased onto this group through <see cref="Unreal.Core.NetFieldParser.AliasResolver"/>.
/// </summary>
[NetFieldExportGroup(GenericContainer.GroupPath, minimalParseMode: ParseMode.Minimal)]
public class GenericContainer : BaseContainer
{
    public const string GroupPath = "__ChestCounter.GenericContainer";
}
