# 零拷贝（游戏服务端）

减少内核态/用户态之间的数据搬运,让数据"原地"到达网卡。

## demo 索引

- ✅ [sendfile/](./sendfile/) — sendfile 系统调用的文件→socket 直传

## 待研究

- [ ] splice / tee 管道零拷贝
- [ ] mmap + write vs sendfile 的选型
- [ ] MSG_ZEROCOPY(UDP/TCP 用户态零拷贝)
