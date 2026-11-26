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

async function getLatestVersionInfo() {
  const relResp = await ghFetch("/releases/latest");
  if (relResp.ok) {
    const rel = await relResp.json();
    return {
      status: 200,
      payload: {
        latest_version: rel.tag_name,
        changelog: (rel.body || rel.name || "").trim(),
        updated_at: rel.published_at || rel.created_at || "",
        source: "github_release",
      },
    };
  }

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
    return { status: 404, payload: { error: "No valid SemVer tags found on GitHub" } };
  }

  validTags.sort((a, b) => cmpSemverDesc(a.name, b.name));
  const latest = validTags[0];
  let changelog = "";
  let updatedAt = "";

  if (latest.commit && latest.commit.sha) {
    const commitResp = await ghFetch(`/commits/${latest.commit.sha}`);
    if (commitResp.ok) {
      const commitData = await commitResp.json();
      changelog = (commitData.commit && commitData.commit.message ? commitData.commit.message : "").trim();
      updatedAt =
        (commitData.commit && commitData.commit.committer && commitData.commit.committer.date) || "";
    }
  }

  return {
    status: 200,
    payload: {
      latest_version: latest.name,
      changelog,
      updated_at: updatedAt,
      source: "github_tag",
    },
    validTags,
  };
}

module.exports = async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  res.setHeader("Cache-Control", "public, s-maxage=60, stale-while-revalidate=300");

  try {
    const reqUrl = String((req && req.url) || "");
    const wantHistory = reqUrl.includes("history=1") || reqUrl.includes("all=1");

    const latestRes = await getLatestVersionInfo();
    if (!wantHistory) {
      return res.status(latestRes.status).json(latestRes.payload);
    }

    // 官网历史版本与最新开发提交记录
    const [relsResp, commitsResp] = await Promise.all([
      ghFetch("/releases?per_page=10"),
      ghFetch("/commits?per_page=8"),
    ]);

    let releases = [];
    if (relsResp.ok) {
      const rawRels = await relsResp.json();
      if (Array.isArray(rawRels) && rawRels.length > 0) {
        releases = rawRels.map((r) => ({
          version: r.tag_name,
          title: r.name || r.tag_name,
          changelog: (r.body || "").trim(),
          date: r.published_at || r.created_at || "",
          url: r.html_url || "",
        }));
      }
    }

    if (releases.length === 0 && latestRes.status === 200) {
      releases = [
        {
          version: latestRes.payload.latest_version,
          title: latestRes.payload.latest_version,
          changelog: latestRes.payload.changelog,
          date: latestRes.payload.updated_at,
          url: `https://github.com/${REPO}/releases/tag/${encodeURIComponent(latestRes.payload.latest_version)}`,
        },
      ];
    }

    let commits = [];
    if (commitsResp.ok) {
      const rawCommits = await commitsResp.json();
      if (Array.isArray(rawCommits)) {
        commits = rawCommits.map((c) => ({
          sha: String(c.sha || "").slice(0, 7),
          message: String((c.commit && c.commit.message) || "").split("\n")[0],
          date: (c.commit && c.commit.committer && c.commit.committer.date) || "",
          url: c.html_url || "",
        }));
      }
    }

    return res.status(200).json({
      latest: latestRes.status === 200 ? latestRes.payload : null,
      releases,
      commits,
      repo: REPO,
    });
  } catch (err) {
    return res.status(502).json({ error: err.message || "Failed to fetch GitHub version" });
  }
};
