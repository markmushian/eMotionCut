import test from 'node:test';
import assert from 'node:assert/strict';
import {onboardingFailureText} from './onboarding.ts';

test('部署检查：系统级错误换成用户看得懂的话，不把临时路径甩到界面上',()=>{
 const eperm=Object.assign(Error('EPERM: Permission denied: \\\\?\\C:\\Users\\someone\\AppData\\Local\\Temp\\emotioncut-agent-version-Pf5oED'),{code:'EPERM'});
 const text=onboardingFailureText(eperm);
 assert.match(text,/这一项没查成/);
 assert.match(text,/临时目录/);
 assert.ok(!text.includes('\\?\\'));
 assert.ok(!text.includes('AppData'));
});

test('部署检查：磁盘/句柄/临时目录缺失各有提示，未知错误保留原话',()=>{
 assert.match(onboardingFailureText(Error('ENOSPC: no space left on device, write')),/磁盘空间不足/);
 assert.match(onboardingFailureText(Error('EMFILE: too many open files, open')),/同时打开的文件太多/);
 assert.match(onboardingFailureText(Error('ENOENT: no such file or directory, mkdir C:\\Temp\\x')),/临时目录不存在/);
 assert.match(onboardingFailureText(Error('命令行没有返回内容')),/命令行没有返回内容/);
 assert.match(onboardingFailureText(''),/请重新检查/);
});

test('部署检查：过长的错误只留一段，避免撑破提示框',()=>{
 const text=onboardingFailureText(Error('x'.repeat(400)));
 assert.ok(text.length<200);
});
