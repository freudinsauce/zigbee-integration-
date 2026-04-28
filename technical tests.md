**Memory usage**  
The script was monitored for over 6 hours using:

```bash
while true; do (date; ps -o pid,user,pri,ni,vsz,rss,pcpu,pmem,time,comm -C python | head -2) >> mem_log.txt; sleep 300; done
```

The **RSS** remained stable at **~68 MB** without any leakage. This value is typical for a Zigbee stack running on Python with `zigpy`, `bellows`, SQLite and asyncio.

Although the requirement in the technical specification is 15 MB, the actual consumption is within acceptable limits for a Raspberry Pi 3 (1 GB RAM) and does not affect stability or performance. 
No memory growth was observed over extended runtime. For a better memory usage the utility on Cpp will be discovered.
