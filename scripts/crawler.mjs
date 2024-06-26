#!/usr/bin/env node

/**
 * Crawler document website.
 * 
 * The script can be used in following scenarios:
 * 1. Generate knowledge.json for the agent
 * > node scripts/crawler.mjs https://github.com/reactjs/react.dev/tree/main/src/content/reference tmp/knowledge.json
 * 2. To be used as a `recursive_url` document loader of AIChat
 * > recursive_url: 'node <path-to-llm-functions>/scripts/crawler.mjs $1 $2'
 */

// DEPS: npm i @octokit/rest cheerio  html-to-text node-fetch https-proxy-agent

import { Octokit } from "@octokit/rest";
import * as cheerio from "cheerio";
import { URL } from "node:url";
import { writeFileSync } from "node:fs";
import { compile } from "html-to-text";
import fetch from "node-fetch";
import { HttpsProxyAgent } from "https-proxy-agent";

const compiledConvert = compile({ wordwrap: false, selectors: [{ selector: 'a', options: { ignoreHref: true } }] });

const MAX_DEPTH = parseInt(process.env.CRAWLER_MAX_DEPTH) || 3;;

const MAX_CONCURRENT = parseInt(process.env.CRAWLER_MAX_CONCURRENT) || 5;

const IGNORE_LINKS = new Set();

const IGNORE_PATHS_ENDING_IN = [
  "search.html",
  "search",
  "changelog",
  "changelog.html",
];

let fetchOptions = {
  headers: { "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36" },
};

async function main() {
  const [startUrlRaw, outfile] = process.argv.slice(2);
  if (!startUrlRaw || !outfile) {
    console.log("Usage: ./crawler.mjs <url> <outfile>");
    process.exit(1);
  }
  if (startUrlRaw.startsWith("https://") && process.env["HTTPS_PROXY"]) {
    fetchOptions["agent"] = new HttpsProxyAgent(process.env["HTTPS_PROXY"]);
  }
  let pages = [];
  for await (const page of crawlPage(startUrlRaw, MAX_DEPTH)) {
    pages.push(page);
  }
  const output = JSON.stringify(pages, null, 2);
  writeFileSync(outfile, output);
}

/**
 * 
 * @param {String} startUrl 
 * @param {number} maxDepth 
 */
async function* crawlPage(startUrlRaw, maxDepth = 3) {
  if (!startUrlRaw.endsWith("/")) {
    startUrlRaw += "/"
  }
  console.log("Starting crawl from: ", startUrlRaw, " - Max Depth: ", maxDepth);
  const startUrl = new URL(startUrlRaw);
  let paths = [{ path: startUrl.pathname, depth: 0 }];

  if (startUrl.hostname === "github.com") {
    const githubLinks = await crawlGithubRepo(startUrl);
    paths = githubLinks.map((link) => ({
      path: link,
      depth: 1,
    }));
  }

  let index = 0;
  while (index < paths.length) {
    const batch = paths.slice(index, index + MAX_CONCURRENT);

    const promises = batch.map(({ path, depth }) =>
      getLinksFromUrl(startUrlRaw, path).then((links) => ({
        links,
        path,
        depth,
      })),
    );

    const results = await Promise.all(promises);
    for (const {
      links: { markdown, links: linksArray },
      path,
      depth,
    } of results) {
      if (markdown !== "" && depth <= maxDepth) {
        yield {
          path: new URL(path, startUrl).toString(),
          markdown,
        };
      }

      if (depth < maxDepth) {
        for (let link of linksArray) {
          if (!paths.some((p) => p.path === link)) {
            paths.push({ path: link, depth: depth + 1 });
          }
        }
      }
    }

    index += batch.length;
  }
  console.log("Crawl completed");
}

/**
 * 
 * @param {import("node:url").Url} startUrl 
 * @returns 
 */
async function crawlGithubRepo(startUrl) {
  const octokit = new Octokit({
    auth: undefined,
  });

  const [_, owner, repo, scope, branch, ...pathParts] = startUrl.pathname.split("/");
  if (scope !== "tree" && !branch) {
    throw new Error("Invalid Github URL. It must follow the format: https://github.com/<owner>/<repo>/tree/<branch>/<path>")
  }
  const rootPath = pathParts.join("/");

  const tree = await octokit.request(
    "GET /repos/{owner}/{repo}/git/trees/{tree_sha}",
    {
      owner,
      repo,
      tree_sha: branch,
      headers: {
        "X-GitHub-Api-Version": "2022-11-28",
      },
      recursive: "true",
    },
  );

  const paths = tree.data.tree
    .filter((file) => file.type === "blob" && file.path?.endsWith(".md") && file.path.startsWith(rootPath))
    .map(
      (file) =>
        `https://raw.githubusercontent.com/${owner}/${repo}/${branch}/${file.path}`,
    );

  return paths;
}

/**
 * 
 * @param {String} startUrlRaw 
 * @param {String} path 
 * @returns 
 */
async function getLinksFromUrl(startUrlRaw, path) {
  const location = new URL(path, startUrlRaw).toString();

  console.log(`Crawl ${location}`)

  const response = await fetch(location, fetchOptions);
  const html = await response.text();

  let links = [];

  if (startUrlRaw.includes("github.com")) {
    return {
      markdown: html,
      links,
    };
  }

  const $ = cheerio.load(html);

  IGNORE_LINKS.add(path);
  if (path.endsWith("/")) {
    IGNORE_LINKS.add(`${path}index.html`);
  }

  $("a").each((_, element) => {
    const href = $(element).attr("href");
    if (!href) {
      return;
    }

    const parsedUrl = new URL(href, startUrlRaw);
    if (parsedUrl.toString().startsWith(startUrlRaw)) {
      const link = parsedUrl.pathname;
      if (
        !IGNORE_LINKS.has(link) &&
        !link.includes("#") &&
        !IGNORE_PATHS_ENDING_IN.some((ending) => link.endsWith(ending))
      ) {
        links.push(link);
      }
    }
  });

  links = [...new Set(links)];

  return {
    markdown: compiledConvert(html),
    links,
  };
}

main();
