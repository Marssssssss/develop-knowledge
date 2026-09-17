/**
 * unpinning_agent.js — SSL Pinning 绕过 agent(参考 objection pinning.ts 官方源码结构)
 * 语义对应: sensepost/objection agent/src/android/pinning.ts (7 条 hook 路径)
 */
'use strict';

// 1) 通用: registerClass 空 TrustManager + 改写 SSLContext.init 的 trustManager 参数
const sslContextEmptyTrustManager = () => wrapJavaPerform(() => {
  const x509TrustManager = Java.use('javax.net.ssl.X509TrustManager');
  const sSLContext = Java.use('javax.net.ssl.SSLContext');
  // 反检测: 改 classFactory.tempFileNaming.prefix(默认 'frida' 会出现在 /proc/<pid>/maps)
  if (Java.classFactory.tempFileNaming.prefix === 'frida') {
    Java.classFactory.tempFileNaming.prefix = 'onetwothree';
  }
  const TrustManager = Java.registerClass({
    implements: [x509TrustManager],
    methods: {
      checkClientTrusted(chain, authType) { },
      checkServerTrusted(chain, authType) { },   // 空实现 = 不校验
      getAcceptedIssuers() { return []; },
    },
    name: 'com.sensepost.test.TrustManager',
  });
  const TrustManagers = [TrustManager.$new()];
  const SSLContextInit = sSLContext.init.overload(
    '[Ljavax.net.ssl.KeyManager;', '[Ljavax.net.ssl.TrustManager;', 'java.security.SecureRandom');
  SSLContextInit.implementation = function (keyManager, trustManager, secureRandom) {
    SSLContextInit.call(this, keyManager, TrustManagers, secureRandom);  // 换成空 TM 再链回原 init
  };
});

// 2) OkHttp 3.x: CertificatePinner.check 空实现(不抛异常)
const okHttp3CertificatePinnerCheck = () => wrapJavaPerform(() => {
  try {
    const certificatePinner = Java.use('okhttp3.CertificatePinner');
    const CertificatePinnerCheck =
      certificatePinner.check.overload('java.lang.String', 'java.util.List');
    CertificatePinnerCheck.implementation = function () { };   // 不 throw
  } catch (err) {
    if (String(err).indexOf('ClassNotFoundException') !== -1) return null; // 类不存在 -> 跳过
    throw err;
  }
});

// 2b) 新版 OkHttp: check$okhttp(Kotlin 元符号方法名)
// certificatePinner.check$okhttp.implementation = function () { };

// 3) Android 7+ conscrypt: TrustManagerImpl.verifyChain 直接返回入参链
const trustManagerImplVerifyChainCheck = () => wrapJavaPerform(() => {
  const trustManagerImpl = Java.use('com.android.org.conscrypt.TrustManagerImpl');
  trustManagerImpl.verifyChain.implementation = function (untrustedChain, ...rest) {
    return untrustedChain;      // 跳过全部校验逻辑,原样返回
  };
});

// 4) Android 7+: checkTrustedRecursive 返回空 ArrayList
// trustManagerImpl.checkTrustedRecursive.implementation = function (...) {
//   return Java.use('java.util.ArrayList').$new();
// };

// 5) PhoneGap: SSLCertificateChecker.execute -> 回调 CONNECTION_SECURE
// sslCertificateChecker.execute.overload(...).implementation =
//   function (str, jsonArray, callBackContext) {
//     callBackContext.success('CONNECTION_SECURE');
//     return true;
//   };

// 主入口: job 模型 —— addImplementation 只挂成功的 hook(类不存在时为 undefined)
export const disable = async (quiet) => {
  const job = new Job(identifier(), 'android-sslpinning-disable');
  job.addImplementation(await sslContextEmptyTrustManager(job.identifier));
  job.addImplementation(await okHttp3CertificatePinnerCheck(job.identifier));
  job.addImplementation(await okHttp3CertificatePinnerCheckOkHttp(job.identifier));
  job.addImplementation(await appceleratorTitaniumPinningTrustManager(job.identifier));
  job.addImplementation(await trustManagerImplVerifyChainCheck(job.identifier));
  job.addImplementation(await trustManagerImplCheckTrustedRecursiveCheck(job.identifier));
  job.addImplementation(await phoneGapSSLCertificateChecker(job.identifier));
  jobs.add(job);
};
// 对比: frida-multiple-unpinning(CodeShare) 覆盖更多场景——
// 它会动态检测 SSLPeerUnverifiedException 的实例化并自动 patch 抛出方法。
