"""Use the simulator's host binding instead of the device crypto binding."""

Import("env")


def skip_device_binding(node):
    return None


env.AddBuildMiddleware(skip_device_binding, "*lib/Epub/ContentProtection.cpp")

# LCP parsing and key operations are portable; crypto remains the host binding.
from os.path import join
sdk_content = join(env.subst("$PROJECT_DIR"), "freeink-sdk", "libs", "book", "ContentProtection")
env.AppendUnique(CPPPATH=[join(sdk_content, "include"), join(env.subst("$PROJECT_DIR"), "freeink-sdk", "libs", "network", "JsonSax", "include")])
env.BuildSources(join(env.subst("$BUILD_DIR"), "lcp-license"), join(sdk_content, "src"), src_filter=["+<LcpLicense.cpp>"])
