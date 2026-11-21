import os
import asyncio
import re
import time

import requests
from playwright.async_api import async_playwright
from datetime import datetime
from feishu_utils import FeishuTable

# 飞书配置：从环境变量读取（与 GitHub Secrets 名称完全对应）
FEISHU_APP_ID     = os.environ.get("FEISHU_APP_ID")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET")
FEISHU_BASE_TOKEN = os.environ.get("FEISHU_BASE_TOKEN")
FEISHU_TABLE_ID   = os.environ.get("FEISHU_TABLE_ID")

def get_now_timestamp():
    """获取当前时间的毫秒时间戳"""
    return int(time.time() * 1000)

def parse_date_to_timestamp(date_str):
    """将日期字符串转换为毫秒时间戳，如果失败则返回 None"""
    if not date_str or "详情" in date_str or "待定" in date_str:
        return None
    
    # 清理字符串，只保留日期部分
    date_str = date_str.strip().split(' ')[0]
    
    try:
        # 尝试解析常见的日期格式
        for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%m/%d", "%m-%d"):
            try:
                dt = datetime.strptime(date_str, fmt)
                if dt.year == 1900: # 处理没有年份的情况
                    dt = dt.replace(year=datetime.now().year)
                return int(dt.timestamp() * 1000)
            except ValueError:
                continue
    except:
        pass
    return None

async def scrape_qiuzhifangzhou(page):
    """求职方舟：解决翻页超时和抓取为空的问题"""
    jobs = []
    print("正在连接: 求职方舟...")
    try:
        await page.goto("https://www.qiuzhifangzhou.com/campus", wait_until="networkidle", timeout=60000)

        for i in range(1, 31):  # 限制抓取 30 页
            print(f"  正在处理求职方舟第 {i} 页...")
            await page.wait_for_selector(".ag-row", timeout=10000)

            rows = await page.query_selector_all(".ag-row")
            for row in rows:
                cells = await row.query_selector_all(".ag-cell")
                if len(cells) >= 8:
                    company  = (await cells[1].inner_text()).strip()
                    position = (await cells[2].inner_text()).strip()
                    deadline_str = (await cells[7].inner_text()).strip()
                    
                    link_elem = await cells[2].query_selector("a")
                    link = await link_elem.get_attribute("href") if link_elem else ""
                    if link and not link.startswith("http"):
                        link = "https://www.qiuzhifangzhou.com" + link

                    if company and position:
                        job = {
                            "公司名称": company,
                            "岗位": position,
                        }
                        if link:
                            job["投递链接"] = {
                                "link": link,
                                "text": "点击投递",
                            }
                        deadline_ts = parse_date_to_timestamp(deadline_str)
                        if deadline_ts is not None:
                            job["结束时间"] = deadline_ts
                        jobs.append(job)

            # 智能翻页
            can_next = await page.evaluate("""() => {
                const btn = document.querySelector('[ref="btNext"]');
                if (btn && !btn.disabled && !btn.classList.contains('ag-disabled')) {
                    btn.click();
                    return true;
                }
                return false;
            }""")
            if not can_next:
                break
            await asyncio.sleep(2)
    except Exception as e:
        print(f"求职方舟出错: {e}")
    print(f"求职方舟抓取完成，共 {len(jobs)} 条")
    return jobs


async def scrape_givemeoc(page):
    """GiveMeOC：解决只抓 30 条和翻页失效的问题"""
    jobs = []
    print("正在连接: GiveMeOC...")
    try:
        for p in range(1, 11):  # 抓取前 10 页
            print(f"  正在处理 GiveMeOC 第 {p} 页...")
            await page.goto(f"https://www.givemeoc.com/?paged={p}", wait_until="domcontentloaded")
            await page.wait_for_selector("table", timeout=10000)

            # 登录前的列表也有数据行，表头可能由独立的组件渲染。
            columns = {"公司名称": 0, "所属行业": 2, "招聘类型": 3, "岗位": 6, "投递截止": 9}
            company_types = {"央国企", "民企", "事业单位", "银行", "外企/合资", "社会机构"}
            rows = await page.query_selector_all("table tr")
            page_jobs_before = len(jobs)
            for row in rows:
                cells = await row.query_selector_all("td")
                if len(cells) < 10:
                    continue
                company_type = (await cells[1].inner_text()).strip()
                if company_type not in company_types:
                    continue
                company = (await cells[columns["公司名称"]].inner_text()).strip()
                position = (await cells[columns["岗位"]].inner_text()).strip()
                if not company or company in company_types or not position:
                    continue

                job = {"公司名称": company, "岗位": position}
                industry = (await cells[columns["所属行业"]].inner_text()).strip()
                if industry and industry != "-":
                    job["公司行业"] = [industry]
                recruitment_type = (await cells[columns["招聘类型"]].inner_text()).strip()
                if recruitment_type and recruitment_type != "-":
                    job["招聘类型"] = recruitment_type
                location = (await cells[5].inner_text()).strip()
                locations = [
                    part.strip() for part in re.split(r"[、，,；;\n]+", location)
                    if part.strip() and part.strip() != "-"
                ]
                if locations:
                    job["工作地点"] = list(dict.fromkeys(locations))

                audience = await cells[4].inner_text()
                cohorts = re.findall(r"20\d{2}", audience)
                if cohorts:
                    job["届次"] = [f"{year}届" for year in dict.fromkeys(cohorts)]

                note = ""
                if len(cells) > 14:
                    note = (await cells[14].inner_text()).strip()
                    if note and note != "-":
                        job["备注"] = note

                degree_pattern = r"(?:最低学历[：:]?\s*|学历要求[：:]?\s*)?(大专|专科|本科|硕士|博士)(\s*(?:及以上|以上|起))?"
                degree = re.fullmatch(degree_pattern, note)
                if degree is None:
                    degree = re.search(r"(大专|专科|本科|硕士|博士)(\s*(?:及以上|以上|起))\s*$", audience.strip())
                if degree:
                    minimum = "大专" if degree[1] == "专科" else degree[1]
                    job["学历要求"] = minimum + ("及以上" if degree[2] else "")

                updated_at = parse_date_to_timestamp(await cells[8].inner_text())
                if updated_at is not None:
                    job["更新时间"] = updated_at

                deadline = parse_date_to_timestamp(
                    await cells[columns["投递截止"]].inner_text()
                )
                if deadline is not None:
                    job["结束时间"] = deadline
                jobs.append(job)
            if len(jobs) == page_jobs_before:
                print(f"GiveMeOC 第 {p} 页没有符合列结构的数据行")
            await asyncio.sleep(1)
    except Exception as e:
        print(f"GiveMeOC 出错: {e}")
    print(f"GiveMeOC 抓取完成，共 {len(jobs)} 条")
    return jobs


# These links are for the entire 2027 fall recruiting campaign.
CAMPUS_LINKS = {
    "极兔速递": (
        "https://career.gdufs.edu.cn/index.php/web/index/preach-detail?id=1701",
        "https://jtexpress.jobs.feishu.cn/478610/",
    ),
    "GE医疗": (
        "https://career.hebut.edu.cn/home/correcruit/content/id/80189.html",
        "https://app.mokahr.com/campus-recruitment/gehc/142250#/",
    ),
}


def sync_jobs(feishu, table_id, jobs):
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{feishu.base_token}/tables/{table_id}/records"
    records = {}
    params = {"page_size": 500}
    while True:
        response = requests.get(url, headers=feishu._headers(), params=params, timeout=30)
        response.raise_for_status()
        result = response.json()
        if result.get("code") != 0:
            raise RuntimeError(f"读取飞书记录失败：{result}")
        data = result["data"]
        for record in data["items"]:
            fields = record["fields"]
            key = (fields.get("公司名称"), fields.get("岗位"))
            if all(isinstance(value, str) and value for value in key):
                records.setdefault(key, []).append(record)
        if not data.get("has_more"):
            break
        params["page_token"] = data["page_token"]

    additions = {}
    updates = {}
    for job in jobs:
        key = (job["公司名称"], job["岗位"])
        links = CAMPUS_LINKS.get(key[0])
        if links and job.get("招聘类型") == "秋招" and "2027届" in job.get("届次", []):
            job.setdefault("公告链接", {"link": links[0], "text": "招聘公告"})
            job.setdefault("投递链接", {"link": links[1], "text": "校招入口"})
        if key not in records:
            additions.setdefault(key, job)
            continue
        for record in records[key]:
            fields = record["fields"]
            if fields.get("招聘类型") and fields["招聘类型"] != job.get("招聘类型"):
                continue
            if fields.get("届次") and not set(fields["届次"]).intersection(job.get("届次", [])):
                continue
            patch = {}
            if job.get("更新时间"):
                patch["更新时间"] = job["更新时间"]
            for name in ("公告链接", "投递链接"):
                if name in job and not fields.get(name):
                    patch[name] = job[name]
            if patch:
                updates.setdefault(record["record_id"], {}).update(patch)

    pending = [{"record_id": record_id, "fields": fields} for record_id, fields in updates.items()]
    for start in range(0, len(pending), 500):
        batch = pending[start:start + 500]
        response = requests.post(f"{url}/batch_update", headers=feishu._headers(),
                                 json={"records": batch}, timeout=30)
        response.raise_for_status()
        result = response.json()
        if result.get("code") != 0:
            raise RuntimeError(f"更新飞书记录失败：{result}")
        if len(result["data"]["records"]) != len(batch):
            raise RuntimeError("飞书返回的更新条数与请求不一致")

    written = feishu.batch_add_records(table_id, list(additions.values()))
    if written != len(additions):
        raise RuntimeError(f"飞书新增不完整：{written}/{len(additions)}")
    print(f"飞书同步完成：新增 {written} 条，更新 {len(updates)} 条")


async def main():
    # 检查必要的环境变量
    missing = [k for k, v in {
        "FEISHU_APP_ID": FEISHU_APP_ID,
        "FEISHU_APP_SECRET": FEISHU_APP_SECRET,
        "FEISHU_BASE_TOKEN": FEISHU_BASE_TOKEN,
        "FEISHU_TABLE_ID": FEISHU_TABLE_ID
    }.items() if not v]

    if missing:
        raise EnvironmentError(f"缺少必要的环境变量: {missing}")

    print(f"[配置] BASE_TOKEN={FEISHU_BASE_TOKEN}, TABLE_ID={FEISHU_TABLE_ID}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        all_jobs = []

        qiuzhi_page = await context.new_page()
        all_jobs.extend(await scrape_qiuzhifangzhou(qiuzhi_page))
        await qiuzhi_page.close()

        givemeoc_page = await context.new_page()
        all_jobs.extend(await scrape_givemeoc(givemeoc_page))
        await givemeoc_page.close()

        print(f"\n抓取完成！总计 {len(all_jobs)} 条记录。")
        
        if all_jobs:
            feishu = FeishuTable(FEISHU_APP_ID, FEISHU_APP_SECRET, FEISHU_BASE_TOKEN)
            sync_jobs(feishu, FEISHU_TABLE_ID, all_jobs)
        else:
            raise RuntimeError("两个来源均未抓到记录，未写入飞书。")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
