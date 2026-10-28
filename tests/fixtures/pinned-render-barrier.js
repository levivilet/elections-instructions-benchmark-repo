// Extracted from locked LVCE v0.126.0 diagnostic renderer; fail-closed patch fixture.
// src/parts/RendererFrameScheduler/RendererFrameScheduler.js
var kAddCssStyleSheet, kDispose2, kSendMultiple, kSetCss2, kSetPatches2, minimumFrameDuration, maximumFrameWait, state3, enqueue, waitForEligibleFrame, invokeWithCacheRecovery, runFrame, sealPendingFrame, invokeDirectRendererCommand, invokeCssStyleSheet, reset2, sendMultiple, invoke, invokeAndTransfer;
var init_RendererFrameScheduler = __esm({
  "src/parts/RendererFrameScheduler/RendererFrameScheduler.js"() {
    "use strict";
    init_IsTest();
    init_NormalizeRendererCommands();
    init_RequestAnimationFrame();
    init_Timestamp();
    kAddCssStyleSheet = "Css.addCssStyleSheet";
    kDispose2 = "Viewlet.dispose";
    kSendMultiple = "Viewlet.sendMultiple";
    kSetCss2 = "Viewlet.setCss";
    kSetPatches2 = "Viewlet.setPatches";
    minimumFrameDuration = 16;
    maximumFrameWait = 100;
    state3 = {
      frameCount: 0,
      lastFrameTime: Number.NEGATIVE_INFINITY,
      /** @type {{commands: any[], promise: Promise<any>} | undefined} */
      pendingFrame: void 0,
      /** @type {any} */
      rpc: void 0,
      /** @type {Promise<any>} */
      tail: Promise.resolve()
    };
    enqueue = (operation) => {
      const promise = state3.tail.then(operation);
      state3.tail = promise.catch(() => {
      });
      return promise;
    };
    waitForEligibleFrame = () => {
      if (isTest()) {
        return Promise.resolve(now());
      }
      return new Promise((resolve3) => {
        let settled = false;
        let frameId;
        const finish = (timestamp) => {
          if (settled) return;
          settled = true;
          clearTimeout(timer);
          cancelAnimationFrame(frameId);
          resolve3(timestamp);
        };
        const handleFrame = (timestamp) => {
          if (settled) return;
          if (timestamp - state3.lastFrameTime < minimumFrameDuration) {
            frameId = requestAnimationFrame(handleFrame);
            return;
          }
          finish(timestamp);
        };
        const timer = setTimeout(() => finish(now()), maximumFrameWait);
        frameId = requestAnimationFrame(handleFrame);
      });
    };
    invokeWithCacheRecovery = async (method, ...params) => {
      try {
        return await state3.rpc.invoke(method, ...params);
      } catch (error2) {
        reset();
        throw error2;
      }
    };
    runFrame = async (frame) => {
      const timestamp = await waitForEligibleFrame();
      if (state3.pendingFrame === frame) {
        state3.pendingFrame = void 0;
      }
      const commands2 = normalizeCommands(frame.commands);
      if (commands2.length === 0) {
        return;
      }
      state3.frameCount++;
      try {
        return await invokeWithCacheRecovery(kSendMultiple, commands2);
      } finally {
        state3.lastFrameTime = timestamp;
      }
    };
    sealPendingFrame = () => {
      state3.pendingFrame = void 0;
    };
    invokeDirectRendererCommand = async (method, params) => {
      const commands2 = normalizeCommands([[method, ...params]]);
      if (commands2.length === 0) {
        return;
      }
      const [command] = commands2;
      return invokeWithCacheRecovery(command[0], ...command.slice(1));
    };
    invokeCssStyleSheet = async (id12, text2) => {
      if (getCssText(id12) === text2) {
        return;
      }
      setCssText(id12, text2);
      return invokeWithCacheRecovery(kAddCssStyleSheet, id12, text2);
    };
    reset2 = (rpc2) => {
      reset();
      state3.frameCount = 0;
      state3.lastFrameTime = Number.NEGATIVE_INFINITY;
      state3.pendingFrame = void 0;
      state3.rpc = rpc2;
      state3.tail = Promise.resolve();
    };
    sendMultiple = (commands2) => {
      if (state3.pendingFrame) {
        state3.pendingFrame.commands.push(...commands2);
        return state3.pendingFrame.promise;
      }
      const frame = {
        commands: [...commands2],
        promise: Promise.resolve()
      };
      state3.pendingFrame = frame;
      frame.promise = enqueue(() => runFrame(frame));
      return frame.promise;
    };
    invoke = (method, ...params) => {
      if (method === kSendMultiple) {
        return sendMultiple(params[0]);
      }
      sealPendingFrame();
      return enqueue(async () => {
        if (method === kAddCssStyleSheet) {
          return invokeCssStyleSheet(params[0], params[1]);
        }
        if (method === kDispose2 || method === kSetCss2 || method === kSetPatches2) {
          return invokeDirectRendererCommand(method, params);
        }
        return invokeWithCacheRecovery(method, ...params);
      });
    };
    invokeAndTransfer = (method, ...params) => {
      sealPendingFrame();
      return enqueue(async () => {
        try {
          return await state3.rpc.invokeAndTransfer(method, ...params);
        } catch (error2) {
          reset();
          throw error2;
        }
      });
    };
  }
});


// src/parts/RendererProcess/RendererProcess.js
__export(ViewletEditorText_ipc_exports, {
});
    executeViewletCommandInternal = async (uid, fnName, args, expectedInstance) => {
      const instance = getInstance(uid);
      if (expectedInstance && instance !== expectedInstance) return;
      if (!instance) {
        if (fnName !== HandleBlur && fnName !== "__renderPending" && fnName !== "updateGitIgnoredUris") {
          warn3(`cannot execute ${fnName} instance not found ${uid}`);
        }
        return;
      }
      const fn2 = await getFn(instance.factory, fnName);
      const oldState = instance.state;
      const newState = await fn2(oldState, ...args);
      if (newState === oldState) return;
      if (getInstance(uid) !== instance) return;
      const nextState = "newState" in newState ? newState.newState : newState;
      const preparationCommands = instance.factory.prepareRender ? await instance.factory.prepareRender(nextState) : [];
      const actualNewState = rebaseState(oldState, instance.state, nextState);
      if (oldState === actualNewState) {
        return;
      }
      if (getInstance(uid) !== instance) {
        return;
      }
      const commands2 = [...preparationCommands, ..."newState" in newState ? newState.commands : []];
      const renderedState = instance.renderedState;
      commands2.push(...render(instance.factory, renderedState, actualNewState));
      updateDynamicFocusContext(commands2);
      setRenderedState(uid, actualNewState);
      if (commands2.length > 0) {
        await invoke2(
          /* Viewlet.sendMultiple */
          "Viewlet.sendMultiple",
          /* commands */
          commands2
        );
      }
      if (getInstance(uid) === instance && instance.factory.afterRender) {
        await instance.factory.afterRender(renderedState, actualNewState);
      }
    };
