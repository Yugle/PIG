const REPO = process.env.GITHUB_REPO || "Yugle/PIG";

function parseSemver(tag) {
  const clean = String(tag || "").trim().replace(/^v/i, "");
  const m = clean.match(/^(\d+)\.(\d+)(?:\.(\d+))?/);
  if (!m) return [0, 0, 0];
  return [Number(m[1]), Number(m[2]), Number(m[3] || 0)];
}

function cmpSemverDesc(a, b) {
  const pa = parseSemver(a);
  const pb = parseSemver(b);
  for (let i = 0; i < 3; i++) {
    if (pb[i] !== pa[i]) return pb[i] - pa[i];
  }
  return 0;
}

async function ghFetch(path) {
  const headers = {
    "User-Agent": "PIG-OTA-Vercel/1.0",
    "Accept": "application/vnd.github+json",
  };
  if (process.env.GITHUB_TOKEN) {
    headers["Authorization"] = `Bearer ${process.env.GITHUB_TOKEN}`;
  }
  return fetch(`https://api.github.com/repos/${REPO}${path}`, { headers });
}

module.exports = async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  res.setHeader("Cache-Control", "public, s-maxage=60, stale-while-revalidate=300");

  try {
    // 1. 优先读取 GitHub Latest Release（含完整 Release Notes）
    const relResp = await ghFetch("/releases/latest");
    if (relResp.ok) {
      const rel = await relResp.json();
      return res.status(200).json({
        latest_version: rel.tag_name,
        changelog: (rel.body || rel.name || "").trim(),
        updated_at: rel.published_at || rel.created_at || "",
        source: "github_release",
      });
    }

    // 2. 若未创建 Release（404），直接读取 Git Tags 并捞取对应提交说明作为 changelog
    if (relResp.status !== 404) {
      throw new Error(`GitHub releases API returned ${relResp.status}`);
    }

    const tagsResp = await ghFetch("/tags?per_page=20");
    if (!tagsResp.ok) {
      throw new Error(`GitHub tags API returned ${tagsResp.status}`);
    }
    const tags = await tagsResp.json();
    const validTags = (Array.isArray(tags) ? tags : []).filter(
      (t) => parseSemver(t.name).some((n) => n > 0)
    );
    if (validTags.length === 0) {
      return res.status(404).json({ error: "No valid SemVer tags found on GitHub" });
    }

    validTags.sort((a, b) => cmpSemverDesc(a.name, b.name));
    const latest = validTags[0];

    let changelog = "";
    let updatedAt = "";

    // 尝试读取 annotated tag 附注或对应 commit message
    if (latest.commit && latest.commit.sha) {
      const commitResp = await ghFetch(`/commits/${latest.commit.sha}`);
      if (commitResp.ok) {
        const commitData = await commitResp.json();
        changelog = (commitData.commit && commitData.commit.message ? commitData.commit.message : "").trim();
        updatedAt =
          (commitData.commit && commitData.commit.committer && commitData.commit.committer.date) || "";
      }
    }

    return res.status(200).json({
      latest_version: latest.name,
      changelog,
      updated_at: updatedAt,
      source: "github_tag",
    });
  } catch (err) {
    return res.status(502).json({ error: err.message || "Failed to fetch GitHub version" });
  }
};
