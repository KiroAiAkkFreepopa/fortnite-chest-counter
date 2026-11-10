namespace Unreal.Core;

/// <summary>
/// ChestCounter patch: diagnostic probe. When <see cref="Sink"/> is set, the raw bits of every property / RPC / FastArray
/// that the parser does NOT decode are handed to it, for groups whose path matches <see cref="Filter"/>.
/// Used to discover where a piece of information lives in the replay. Null (disabled) by default.
/// </summary>
public static class RawProbe
{
    public delegate void SinkHandler(uint channelIndex, string group, string field, byte[] data, int bits);

    public static Func<string, bool>? Filter { get; set; }

    public static SinkHandler? Sink { get; set; }

    internal static bool Matches(string path) => Sink != null && Filter?.Invoke(path) == true;
}
