using System.Text;
using System.Text.Json;
using ChestCounter;

namespace ReplayScan;

/// <summary>
/// replay_scan.exe &lt;file.replay&gt;  -> one JSON line on stdout.
///   {"ok":true,"chests":12,"destroyed":1,"total":13,"br":true,"owner":true,"match_start":"...","duration":890,"version":"..."}
///   {"ok":false,"live":true}              the match is still being recorded (no encryption key yet)
///   {"ok":false,"error":"..."}            unreadable
/// Exit codes: 0 read, 3 still live, 1 error, 2 usage.
/// "chests" = opened with E (not broken); "destroyed" = broken instead of opened; "total" = both.
/// </summary>
internal static class Program
{
    private static int Main(string[] args)
    {
        Console.OutputEncoding = new UTF8Encoding(false);
        if (args.Length < 1)
        {
            Print(new() { ["ok"] = false, ["error"] = "usage: replay_scan <file.replay>" });
            return 2;
        }
        var path = args[0];
        if (!File.Exists(path))
        {
            Print(new() { ["ok"] = false, ["error"] = "file not found" });
            return 1;
        }

        var live = HeaderState(path);
        if (live != HeaderResult.Finished)
        {
            Print(new() { ["ok"] = false, ["live"] = live == HeaderResult.Live, ["error"] = live == HeaderResult.Unreadable ? "unreadable header" : "live" });
            return live == HeaderResult.Live ? 3 : 1;
        }

        MatchRecord? record = null;
        Exception? failure = null;
        // Big stack: the parser is recursive.
        var worker = new Thread(() =>
        {
            try
            {
                byte[] bytes;
                using (var fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
                {
                    bytes = new byte[fs.Length];
                    fs.ReadExactly(bytes);
                }
                using var ms = new MemoryStream(bytes, writable: false);
                record = new ReplayAnalyzer().Analyze(ms, Path.GetFileName(path), "");
            }
            catch (Exception e)
            {
                failure = e;
            }
        }, 64 * 1024 * 1024);
        worker.Start();
        if (!worker.Join(TimeSpan.FromMinutes(3)))
        {
            Print(new() { ["ok"] = false, ["error"] = "analysis took too long" });
            return 1;
        }
        if (failure != null || record == null)
        {
            Print(new() { ["ok"] = false, ["error"] = (failure?.Message ?? "no result") });
            return 1;
        }

        var destroyed = record.Chests.Count(c => c.Destroyed);
        Print(new()
        {
            ["ok"] = true,
            ["chests"] = record.ChestsOpened - destroyed,
            ["destroyed"] = destroyed,
            ["total"] = record.ChestsOpened,
            ["br"] = record.IsBattleRoyale,
            ["owner"] = record.IsReplayOwner,
            ["match_start"] = record.MatchStartUtc?.ToString("o"),
            ["duration"] = record.DurationSeconds,
            ["version"] = record.GameVersion,
        });
        return 0;
    }

    private static void Print(Dictionary<string, object?> json) =>
        Console.WriteLine(JsonSerializer.Serialize(json));

    private enum HeaderResult { Finished, Live, Unreadable }

    /// <summary>UE replay header: magic, file version, then (since v7) the custom versions, then LengthInMs,
    /// NetworkVersion, Changelist, FriendlyName, bIsLive, timestamp, compressed, encrypted, key.</summary>
    private static HeaderResult HeaderState(string path)
    {
        try
        {
            using var fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
            using var br = new BinaryReader(fs);
            if (br.ReadUInt32() != 0x1CA2E27F)
            {
                return HeaderResult.Unreadable;
            }
            var fileVersion = br.ReadUInt32();
            if (fileVersion >= 7)
            {
                var count = br.ReadInt32();
                if (count < 0 || count > 64)
                {
                    return HeaderResult.Unreadable;
                }
                fs.Seek(count * 20L, SeekOrigin.Current);
            }
            fs.Seek(12, SeekOrigin.Current);          // LengthInMs, NetworkVersion, Changelist
            var len = br.ReadInt32();                   // FriendlyName
            fs.Seek(len < 0 ? -2L * len : len, SeekOrigin.Current);
            if (br.ReadUInt32() != 0)                   // bIsLive
            {
                return HeaderResult.Live;
            }
            br.ReadInt64();                             // timestamp
            br.ReadUInt32();                            // compressed
            if (br.ReadUInt32() == 0)                   // not encrypted
            {
                return HeaderResult.Finished;
            }
            var keyLen = br.ReadInt32();
            if (keyLen <= 0 || keyLen > 256)
            {
                return HeaderResult.Unreadable;
            }
            return br.ReadBytes(keyLen).All(b => b == 0) ? HeaderResult.Live : HeaderResult.Finished;
        }
        catch
        {
            return HeaderResult.Unreadable;
        }
    }
}
